"""Probe Dense retrieval stability without calling an LLM.

The probe can either open an existing isolated Chroma store or build a new
isolated store and query it before that process exits.  Re-running the probe
against the fresh store then exposes any fresh-process/reopen lifecycle
difference.  Reranker is only loaded when ``--full-chain`` is requested.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
from typing import Any


HERE = Path(__file__).resolve().parent
EXPERIMENT = HERE.parent
V31_ROOT = HERE.parents[2]
PROJECT_ROOT = V31_ROOT.parents[2]
DATASET = V31_ROOT / "questions_v3_120q.json"
CORPUS = V31_ROOT.parent / "corpus"
PROTOCOL = EXPERIMENT / "protocol.json"
HISTORICAL_ROWS = (
    V31_ROOT / "runs/gpu-baseline-120q-v31-k15-20260828/retrieval-k15.jsonl"
)
A_ROWS = (
    EXPERIMENT
    / "results/development/hybrid-b6d904ca148d/rows.jsonl"
)
SELECTED_DIFFERENCE_IDS = (
    "crud-unanswerable-2docs-64fa9b30b82641eb8ecc029d",
    "crud-unanswerable-3docs-64fa9b33b82641eb8ecc141a",
)
TOP_K = 15


def _environment(storage: Path) -> dict[str, str]:
    return {
        "LLM_ENABLED": "false",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "HF_HUB_DISABLE_PROGRESS_BARS": "1",
        "ANONYMIZED_TELEMETRY": "False",
        "DO_NOT_TRACK": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PREWARM_MODELS": "false",
        "TOKENIZERS_PARALLELISM": "false",
        "EMBEDDING_MODEL": "BAAI/bge-large-zh-v1.5",
        "EMBEDDING_DEVICE": "cuda:0",
        "EMBEDDING_BATCH_SIZE": "32",
        "EMBEDDING_QUERY_INSTRUCTION": "",
        "RERANKER_MODEL": "BAAI/bge-reranker-v2-m3",
        "RERANKER_DEVICE": "cuda:0",
        "RERANKER_USE_FP16": "true",
        "RERANKER_BATCH_SIZE": "8",
        "RERANKER_MAX_LENGTH": "512",
        "RETRIEVAL_TRACE_ENABLED": "false",
        "CHUNK_STRATEGY": "recursive",
        "CHUNK_SIZE": "400",
        "CHUNK_OVERLAP": "80",
        "CHUNK_MAX_TOKENS": "480",
        "CHROMA_PERSIST_DIR": str((storage / "chroma").resolve()),
        "METADATA_DB_PATH": str((storage / "metadata.db").resolve()),
        "UPLOAD_DIR": str((storage / "uploads").resolve()),
    }


def _dense_ids(row: dict[str, Any]) -> list[str]:
    stage = next(
        stage for stage in row["trace"]["stages"] if stage["name"] == "dense"
    )
    return [candidate["chunk_id"] for candidate in stage["candidates"]]


def _rows(path: Path, wanted: set[str]) -> dict[str, dict[str, Any]]:
    result = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row["id"] in wanted:
                result[row["id"]] = row
    return result


def _target_ids(scope: str) -> tuple[str, ...]:
    development = tuple(
        json.loads(PROTOCOL.read_text(encoding="utf-8"))["splits"]["development"]
    )
    if scope == "development":
        return development
    if scope == "selected":
        return SELECTED_DIFFERENCE_IDS
    wanted = set(development)
    historical = _rows(HISTORICAL_ROWS, wanted)
    current = _rows(A_ROWS, wanted)
    return tuple(
        question_id
        for question_id in development
        if _dense_ids(historical[question_id]) != _dense_ids(current[question_id])
    )


def _load_questions(target_ids: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    selected = {
        question["id"]: question
        for question in payload["questions"]
        if question["id"] in target_ids
    }
    if set(selected) != set(target_ids):
        raise RuntimeError("diagnostic question IDs are missing")
    return selected


def _db_state(storage: Path) -> dict[str, Any]:
    path = storage / "chroma" / "chroma.sqlite3"
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        max_seq = {
            segment: seq
            for segment, seq in connection.execute(
                "SELECT segment_id, seq_id FROM max_seq_id"
            )
        }
        segments = {
            segment: {"type": kind, "scope": scope}
            for segment, kind, scope in connection.execute(
                "SELECT id, type, scope FROM segments"
            )
        }
        vector_segment = next(
            segment
            for segment, info in segments.items()
            if info["scope"] == "VECTOR"
        )
        metadata_segment = next(
            segment
            for segment, info in segments.items()
            if info["scope"] == "METADATA"
        )
        return {
            "embedding_count": connection.execute(
                "SELECT COUNT(*) FROM embeddings"
            ).fetchone()[0],
            "queue_count": connection.execute(
                "SELECT COUNT(*) FROM embeddings_queue"
            ).fetchone()[0],
            "vector_segment_max_seq_id": max_seq[vector_segment],
            "metadata_segment_max_seq_id": max_seq[metadata_segment],
        }
    finally:
        connection.close()


def _signature(rows: list[dict[str, Any]]) -> list[str]:
    return [row["chunk_id"] for row in rows]


def _distance_map(rows: list[dict[str, Any]]) -> dict[str, float]:
    return {row["chunk_id"]: float(row["distance"]) for row in rows}


def _probe_question(
    question: str,
    repeats: int,
    all_ids: list[str],
    matrix,
) -> dict[str, Any]:
    import numpy as np
    from backend.services import embedding
    from backend.services import vector_store

    vectors: list[Any] = []
    embedding_hashes: list[str] = []
    pipeline_rows: list[list[dict[str, Any]]] = []
    for _ in range(repeats):
        vector = np.asarray(embedding.embed_query(question), dtype="<f4")
        vectors.append(vector)
        embedding_hashes.append(hashlib.sha256(vector.tobytes()).hexdigest())
        pipeline_rows.append(
            vector_store.search_by_embedding(vector.tolist(), top_k=TOP_K)
        )

    fixed_rows = [
        vector_store.search_by_embedding(vectors[0].tolist(), top_k=TOP_K)
        for _ in range(repeats)
    ]
    norms = np.linalg.norm(matrix, axis=1)
    query_norm = np.linalg.norm(vectors[0])
    exact_distances = 1.0 - (matrix @ vectors[0]) / (norms * query_norm)
    exact_order = np.argsort(exact_distances, kind="stable")[:TOP_K]
    exact = [
        {"chunk_id": all_ids[index], "distance": float(exact_distances[index])}
        for index in exact_order
    ]

    def summarize(samples: list[list[dict[str, Any]]]) -> dict[str, Any]:
        signatures: dict[str, dict[str, Any]] = {}
        for rows in samples:
            ids = _signature(rows)
            key = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
            entry = signatures.setdefault(
                key,
                {"count": 0, "chunk_ids": ids, "distances": _distance_map(rows)},
            )
            entry["count"] += 1
        return {
            "unique_signature_count": len(signatures),
            "signatures": signatures,
        }

    return {
        "embedding_unique_hash_count": len(set(embedding_hashes)),
        "embedding_hashes": sorted(set(embedding_hashes)),
        "pipeline": summarize(pipeline_rows),
        "fixed_embedding_ann": summarize(fixed_rows),
        "exact_top15": exact,
    }


def _full_chain_probe(
    questions: dict[str, dict[str, Any]],
    target_ids: tuple[str, ...],
    repeats: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from eval import adapters
    from eval.crud_rag_1000.v3_1 import tuning_runner

    config = json.loads(
        (
            EXPERIMENT
            / "configs/hybrid-b6d904ca148d.json"
        ).read_text(encoding="utf-8")
    )["retrieval"]
    runtime_config = tuning_runner._runtime_config(config)
    results = {}
    for question_id in target_ids:
        stage_signatures: dict[str, set[tuple[str, ...]]] = {}
        selected_signatures: set[tuple[str, ...]] = set()
        for _ in range(repeats):
            payload = adapters.retrieve_only(
                questions[question_id]["question"],
                include_trace=True,
                runtime_config=runtime_config,
            )
            for stage in payload["trace"]["stages"]:
                signature = tuple(
                    candidate["chunk_id"] for candidate in stage["candidates"]
                )
                stage_signatures.setdefault(stage["name"], set()).add(signature)
            selected_signatures.add(tuple(
                row.get("chunk_id") or row.get("metadata", {}).get("chunk_id")
                for row in payload["retrieval"]
            ))
        results[question_id] = {
            "stage_unique_signature_count": {
                name: len(signatures)
                for name, signatures in stage_signatures.items()
            },
            "stage_signatures": {
                name: [list(signature) for signature in sorted(signatures)]
                for name, signatures in stage_signatures.items()
            },
            "selected_unique_signature_count": len(selected_signatures),
            "selected_signatures": [
                list(signature) for signature in sorted(selected_signatures)
            ],
        }
    return results, adapters.get_run_config()["runtime"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--fresh-build", action="store_true")
    parser.add_argument("--full-chain", action="store_true")
    parser.add_argument("--full-repeats", type=int, default=3)
    parser.add_argument(
        "--scope",
        choices=("selected", "affected", "development"),
        default="selected",
    )
    args = parser.parse_args()
    if not 1 <= args.repeats <= 100:
        raise ValueError("repeats must be between 1 and 100")
    if not 1 <= args.full_repeats <= 20:
        raise ValueError("full-repeats must be between 1 and 20")

    storage = args.storage.resolve()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(output)
    if args.fresh_build:
        storage.mkdir(parents=True, exist_ok=False)
    elif not (storage / "chroma" / "chroma.sqlite3").is_file():
        raise FileNotFoundError(storage)

    os.environ.update(_environment(storage))
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(PROJECT_ROOT))
    blocked: list[str] = []

    def deny_network(event, args_):
        if event == "socket.connect" and args_[0].family in (
            socket.AF_INET,
            socket.AF_INET6,
        ):
            blocked.append(event)
            raise PermissionError("Dense diagnostic forbids outbound IP connections")

    sys.addaudithook(deny_network)
    ingest = None
    if args.fresh_build:
        from eval import adapters

        adapters.assert_isolation()
        ingest = adapters.ingest_corpus(CORPUS, structured=True)

    import numpy as np
    from backend.services import embedding
    from backend.services import vector_store

    collection = vector_store._collection
    raw = collection.get(include=["embeddings"])
    all_ids = list(raw["ids"])
    matrix = np.asarray(raw["embeddings"], dtype=np.float32)
    if matrix.shape[0] != 2914 or len(all_ids) != 2914:
        raise RuntimeError(f"unexpected embedding matrix shape: {matrix.shape}")

    target_ids = _target_ids(args.scope)
    questions = _load_questions(target_ids)
    dense_results = {
        question_id: _probe_question(
            questions[question_id]["question"],
            args.repeats,
            all_ids,
            matrix,
        )
        for question_id in target_ids
    }
    full_chain = None
    full_chain_runtime = None
    if args.full_chain:
        full_chain, full_chain_runtime = _full_chain_probe(
            questions,
            target_ids,
            args.full_repeats,
        )
    result = {
        "schema_version": 1,
        "created_at": datetime.now().astimezone().isoformat(),
        "process_id": os.getpid(),
        "mode": "fresh_build_same_process" if args.fresh_build else "reopened_store",
        "storage": str(storage),
        "repeats": args.repeats,
        "top_k": TOP_K,
        "question_scope": args.scope,
        "question_count": len(target_ids),
        "full_chain_enabled": args.full_chain,
        "full_chain_repeats": args.full_repeats if args.full_chain else 0,
        "ingest": ingest,
        "database_state": _db_state(storage),
        "embedding_actual_device": str(embedding._get_model().device),
        "llm_module_imported": "backend.services.llm" in sys.modules,
        "outbound_ip_connections": len(blocked),
        "questions": dense_results,
        "full_chain": full_chain,
        "full_chain_runtime": full_chain_runtime,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({
        "output": str(output),
        "mode": result["mode"],
        "database_state": result["database_state"],
        "embedding_actual_device": result["embedding_actual_device"],
        "question_scope": args.scope,
        "question_count": len(target_ids),
        "full_chain_enabled": args.full_chain,
        "question_summary": {
            "embedding_variant_questions": sum(
                payload["embedding_unique_hash_count"] != 1
                for payload in result["questions"].values()
            ),
            "pipeline_variant_questions": sum(
                payload["pipeline"]["unique_signature_count"] != 1
                for payload in result["questions"].values()
            ),
            "fixed_ann_variant_questions": sum(
                payload["fixed_embedding_ann"]["unique_signature_count"] != 1
                for payload in result["questions"].values()
            ),
        },
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
