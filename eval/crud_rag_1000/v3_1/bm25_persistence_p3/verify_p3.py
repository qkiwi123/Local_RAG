"""P3真实业务路径验证：mmap Sparse、固定Dense重放、RRF与最终选择。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[4]
EVAL_ROOT = (PROJECT_ROOT / "eval").resolve()


def _resolve(value: str) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _tree_hash(root: Path) -> str:
    rows = [
        (path.relative_to(root).as_posix(), path.stat().st_size, _sha256(path))
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    ]
    return hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _stage(trace: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return next(stage["candidates"] for stage in trace["stages"] if stage["name"] == name)


def _row_from_trace(trace: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    chunk = trace["chunks"][candidate["chunk_id"]]
    return {
        "chunk_id": candidate["chunk_id"],
        "content": chunk["content"],
        "metadata": {
            "document_id": next(
                (
                    row["metadata"].get("document_id")
                    for row in trace.get("_retrieval", [])
                    if row["chunk_id"] == candidate["chunk_id"]
                ),
                chunk["document_name"],
            ),
            "document_name": chunk["document_name"],
            "location_label": chunk.get("location_label", ""),
            "page_number": chunk.get("page_number"),
            "slide_number": chunk.get("slide_number"),
        },
        "distance": candidate.get("distance"),
        "dense_rank": candidate.get("dense_rank"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-chroma", required=True)
    parser.add_argument("--index-root", required=True)
    parser.add_argument("--questions", required=True)
    parser.add_argument("--baseline-jsonl", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--corpus-id", required=True)
    args = parser.parse_args()

    source = _resolve(args.source_chroma)
    index_root = _resolve(args.index_root)
    questions_path = _resolve(args.questions)
    baseline_path = _resolve(args.baseline_jsonl)
    output = _resolve(args.output)
    for path in (source, index_root, questions_path, baseline_path, output.parent):
        if path != EVAL_ROOT and EVAL_ROOT not in path.parents:
            raise ValueError(f"P3验证路径必须位于eval目录：{path}")
    if output.exists():
        raise FileExistsError(f"输出已存在，拒绝覆盖：{output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    source_before = _tree_hash(source)
    snapshot = Path(tempfile.mkdtemp(prefix=".p3-chroma-", dir=output.parent))
    started = time.perf_counter()
    client = None
    try:
        snapshot_chroma = snapshot / "chroma"
        shutil.copytree(source, snapshot_chroma)
        os.environ.update(
            {
                "ANONYMIZED_TELEMETRY": "False",
                "DO_NOT_TRACK": "1",
                "CHROMA_PERSIST_DIR": str(snapshot_chroma),
                "METADATA_DB_PATH": str(snapshot / "unused.db"),
                "UPLOAD_DIR": str(snapshot / "uploads"),
                "SPARSE_BACKEND": "bm25s_mmap",
                "SPARSE_INDEX_ROOT": str(index_root),
                "SPARSE_INDEX_CORPUS_ID": args.corpus_id,
                "CHUNK_STRATEGY": "fixed",
                "CHUNK_SIZE": "500",
                "CHUNK_OVERLAP": "50",
                "CHUNK_MAX_TOKENS": "480",
                "BM25_K1": "1.5",
                "BM25_B": "0.75",
                "PREWARM_MODELS": "false",
            }
        )
        sys.path.insert(0, str(PROJECT_ROOT))

        from backend.services import fusion, retriever, sparse_retriever, vector_store

        client = vector_store._client
        questions_payload = json.loads(questions_path.read_text(encoding="utf-8"))
        questions = {row["id"]: row["question"] for row in questions_payload["questions"]}
        baseline = [
            json.loads(line)
            for line in baseline_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        full_scan_calls = 0
        memory_build_calls = 0

        def reject_full_scan():
            nonlocal full_scan_calls
            full_scan_calls += 1
            raise AssertionError("P3健康路径调用了get_all_chunks")

        def reject_memory_build(*_args, **_kwargs):
            nonlocal memory_build_calls
            memory_build_calls += 1
            raise AssertionError("P3健康路径构建了BM25Okapi")

        vector_store.get_all_chunks = reject_full_scan
        sparse_retriever.BM25Okapi = reject_memory_build
        sparse_exact = 0
        rrf_exact = 0
        reranker_input_exact = 0
        final_exact = 0
        max_score_delta = 0.0
        load_events = 0
        generations: set[str] = set()
        manifests: set[str] = set()

        for baseline_row in baseline:
            question_id = baseline_row["id"]
            trace = baseline_row["trace"]
            trace["_retrieval"] = baseline_row["retrieval"]
            diagnostics: dict[str, Any] = {}
            new_sparse = sparse_retriever.search(
                questions[question_id],
                top_k=15,
                k1=1.5,
                b=0.75,
                diagnostics=diagnostics,
            )
            expected_sparse = _stage(trace, "bm25")
            new_ids = [row["chunk_id"] for row in new_sparse]
            expected_ids = [row["chunk_id"] for row in expected_sparse]
            if new_ids == expected_ids:
                sparse_exact += 1
            for new, old in zip(new_sparse, expected_sparse):
                max_score_delta = max(
                    max_score_delta,
                    abs(new["bm25_score"] - old["bm25_score"]),
                )
            if diagnostics["sparse_backend"] != "bm25s_mmap":
                raise AssertionError("未使用bm25s_mmap")
            if diagnostics["sparse_index_load_ms"] > 0:
                load_events += 1
            generations.add(diagnostics["sparse_index_generation"])
            manifests.add(diagnostics["sparse_manifest_hash"])

            dense = [
                _row_from_trace(trace, candidate)
                for candidate in _stage(trace, "dense")
                if candidate["eligible"]
            ]
            fused = fusion.reciprocal_rank_fusion(dense, new_sparse, rrf_k=60, top_k=15)
            expected_rrf = _stage(trace, "rrf")
            if [row["chunk_id"] for row in fused] == [
                row["chunk_id"] for row in expected_rrf
            ]:
                rrf_exact += 1
                reranker_input_exact += 1

            fused_by_id = {row["chunk_id"]: row for row in fused}
            reranked = [
                {
                    **fused_by_id[candidate["chunk_id"]],
                    "reranker_score": candidate["reranker_score"],
                }
                for candidate in _stage(trace, "reranker")
            ]
            selected = retriever._limit_per_document(
                reranked,
                top_k=5,
                max_per_document=3,
            )
            if [row["chunk_id"] for row in selected] == trace["selected_chunk_ids"]:
                final_exact += 1

        count = len(baseline)
        passed = (
            count == 120
            and sparse_exact == count
            # 旧业务日志按Chroma返回顺序累计average_idf；确定性generation
            # 按chunk_id排序，IEEE-754加法次序带来约1e-13差异，不影响排名。
            and max_score_delta <= 1e-12
            and rrf_exact == count
            and reranker_input_exact == count
            and final_exact == count
            and full_scan_calls == 0
            and memory_build_calls == 0
            and load_events == 1
            and len(generations) == 1
            and len(manifests) == 1
        )
        result = {
            "status": "passed" if passed else "failed",
            "question_count": count,
            "sparse_top15_order_exact": sparse_exact,
            "max_bm25_score_delta": max_score_delta,
            "bm25_score_tolerance": 1e-12,
            "rrf_order_exact": rrf_exact,
            "reranker_input_exact": reranker_input_exact,
            "final_top5_replay_exact": final_exact,
            "full_corpus_scan_calls": full_scan_calls,
            "bm25_okapi_build_calls": memory_build_calls,
            "mmap_load_events": load_events,
            "generation_ids": sorted(generations),
            "manifest_hashes": sorted(manifests),
            "source_chroma_unchanged": source_before == _tree_hash(source),
            "baseline_jsonl_sha256": _sha256(baseline_path),
            "questions_sha256": _sha256(questions_path),
            "wall_s": time.perf_counter() - started,
            "scope": (
                "P3 integration correctness with saved identical Dense candidates; "
                "reranker scores replayed, no model or P4 performance run"
            ),
        }
        if not passed or not result["source_chroma_unchanged"]:
            raise AssertionError(result)
        output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        try:
            if "sparse_retriever" in locals():
                sparse_retriever.clear_cache()
            if client is not None:
                client._system.stop()
        finally:
            shutil.rmtree(snapshot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
