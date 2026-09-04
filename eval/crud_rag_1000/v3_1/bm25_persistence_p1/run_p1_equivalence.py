"""P1 isolated BM25S persistence and ranking-equivalence probe.

This script reads the frozen CRUD-RAG 1000-document evaluation storage only.
It never imports the production data paths, writes all artifacts below this
directory, and does not modify application retrieval code or requirements.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import socket
import sys
import time
from typing import Any, Callable, Iterator

import numpy as np
import psutil
from rank_bm25 import BM25Okapi


HERE = Path(__file__).resolve().parent
V31_ROOT = HERE.parent
PROJECT_ROOT = V31_ROOT.parents[2]
DEPS_DIR = HERE / "_deps"
SOURCE_STORAGE = V31_ROOT / "tuning" / "v35-a-baseline-20260829" / "storage"
DATASET_PATH = V31_ROOT / "questions_v3_120q.json"
EXPECTED_DOCUMENTS = 1000
EXPECTED_CHUNKS = 2914
EXPECTED_QUESTIONS = 120
K_VALUES = (1, 5, 15, 50)
K1 = 1.5
B = 0.75
EPSILON = 0.25


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _tree_fingerprint(root: Path) -> dict[str, Any]:
    rows = []
    for path in sorted((item for item in root.rglob("*") if item.is_file())):
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    return {
        "files": len(rows),
        "bytes": sum(row["bytes"] for row in rows),
        "sha256": hashlib.sha256(canonical).hexdigest(),
    }


def _write_json_exclusive(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _write_text_exclusive(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _configure_isolation(storage: Path) -> None:
    required = [
        storage / "chroma",
        storage / "metadata.db",
        storage / "uploads",
        DATASET_PATH,
        DEPS_DIR / "bm25s",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("P1隔离输入缺失：" + ", ".join(missing))
    os.environ.update(
        {
            "CHROMA_PERSIST_DIR": str((storage / "chroma").resolve()),
            "METADATA_DB_PATH": str((storage / "metadata.db").resolve()),
            "UPLOAD_DIR": str((storage / "uploads").resolve()),
            "PREWARM_MODELS": "false",
            "ANONYMIZED_TELEMETRY": "False",
            "DO_NOT_TRACK": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )


def _install_network_guard(blocked: list[dict[str, Any]], started: float) -> None:
    def deny_network(event: str, args: tuple[Any, ...]) -> None:
        if event == "socket.connect" and args[0].family in (
            socket.AF_INET,
            socket.AF_INET6,
        ):
            blocked.append(
                {"event": event, "at_s": round(time.perf_counter() - started, 6)}
            )
            raise PermissionError("P1 BM25探针禁止出站网络")

    sys.addaudithook(deny_network)


def _stable_positive_ranking(
    scores: np.ndarray, chunk_ids: list[str], k: int
) -> list[tuple[str, float]]:
    return sorted(
        (
            (chunk_ids[index], float(score))
            for index, score in enumerate(scores)
            if float(score) > 0.0
        ),
        key=lambda item: (-item[1], item[0]),
    )[:k]


def _document_frequencies(corpus_tokens: list[list[str]]) -> Counter[str]:
    frequencies: Counter[str] = Counter()
    for tokens in corpus_tokens:
        frequencies.update(set(tokens))
    return frequencies


def _rank_bm25_average_idf(corpus_tokens: list[list[str]]) -> float:
    frequencies = _document_frequencies(corpus_tokens)
    size = len(corpus_tokens)
    raw = [math.log(size - value + 0.5) - math.log(value + 0.5) for value in frequencies.values()]
    return sum(raw) / len(raw)


@contextmanager
def _bm25s_idf_selector(
    bm25s_module: Any,
    *,
    average_idf: float,
) -> Iterator[None]:
    original: Callable[[str], Callable[..., float]] = bm25s_module._select_idf_scorer

    def rank_bm25_idf(df: int, N: int) -> float:
        raw = math.log(N - df + 0.5) - math.log(df + 0.5)
        return EPSILON * average_idf if raw < 0 else raw

    def select(method: str) -> Callable[..., float]:
        if method == "rank_bm25_epsilon_025":
            return rank_bm25_idf
        return original(method)

    bm25s_module._select_idf_scorer = select
    try:
        yield
    finally:
        bm25s_module._select_idf_scorer = original


def _build_bm25s(
    bm25s_module: Any,
    corpus_tokens: list[list[str]],
    *,
    variant: str,
    average_idf: float,
) -> tuple[Any, float]:
    if variant == "stock":
        idf_method = "robertson"
        context = nullcontext()
    elif variant == "rank_bm25_compat":
        idf_method = "rank_bm25_epsilon_025"
        context = _bm25s_idf_selector(
            bm25s_module,
            average_idf=average_idf,
        )
    else:
        raise ValueError(f"未知variant：{variant}")

    started = time.perf_counter()
    with context:
        model = bm25s_module.BM25(
            method="atire",
            idf_method=idf_method,
            k1=K1,
            b=B,
            dtype="float64",
            backend="numpy",
            csc_backend="numpy",
        )
        model.index(corpus_tokens, show_progress=False)
    return model, time.perf_counter() - started


def _compare_variant(
    *,
    name: str,
    model: Any,
    oracle: BM25Okapi,
    questions: list[dict[str, Any]],
    chunk_ids: list[str],
) -> dict[str, Any]:
    exact_counts = {str(k): 0 for k in K_VALUES}
    set_counts = {str(k): 0 for k in K_VALUES}
    mismatches: list[dict[str, Any]] = []
    max_score_delta = 0.0
    total_started = time.perf_counter()
    per_query_s: list[float] = []

    from backend.services.sparse_retriever import tokenize

    for question in questions:
        query_tokens = tokenize(question["question"])
        oracle_scores = np.asarray(oracle.get_scores(query_tokens), dtype=np.float64)
        query_started = time.perf_counter()
        candidate_scores = np.asarray(model.get_scores(query_tokens), dtype=np.float64)
        candidate_top50 = _stable_positive_ranking(candidate_scores, chunk_ids, 50)
        per_query_s.append(time.perf_counter() - query_started)
        oracle_top50 = _stable_positive_ranking(oracle_scores, chunk_ids, 50)
        max_score_delta = max(
            max_score_delta,
            float(np.max(np.abs(oracle_scores - candidate_scores))),
        )
        for k in K_VALUES:
            oracle_ids = [item[0] for item in oracle_top50[:k]]
            candidate_ids = [item[0] for item in candidate_top50[:k]]
            if oracle_ids == candidate_ids:
                exact_counts[str(k)] += 1
            if set(oracle_ids) == set(candidate_ids):
                set_counts[str(k)] += 1
            if oracle_ids != candidate_ids and len(mismatches) < 30:
                first = next(
                    (
                        index
                        for index, pair in enumerate(zip(oracle_ids, candidate_ids))
                        if pair[0] != pair[1]
                    ),
                    min(len(oracle_ids), len(candidate_ids)),
                )
                mismatches.append(
                    {
                        "question_id": question["id"],
                        "k": k,
                        "first_difference_rank": first + 1,
                        "oracle_ids": oracle_ids,
                        "candidate_ids": candidate_ids,
                    }
                )

    ordered_all = all(value == len(questions) for value in exact_counts.values())
    return {
        "name": name,
        "ordered_equivalent": ordered_all,
        "exact_order_questions": exact_counts,
        "same_set_questions": set_counts,
        "question_count": len(questions),
        "max_absolute_score_delta": max_score_delta,
        "query_total_s": time.perf_counter() - total_started,
        "candidate_scoring_and_top50_s": {
            "mean": float(np.mean(per_query_s)),
            "p50": float(np.percentile(per_query_s, 50)),
            "p95": float(np.percentile(per_query_s, 95)),
            "max": max(per_query_s),
        },
        "first_mismatches": mismatches,
    }


def _save_and_reload(
    bm25s_module: Any,
    *,
    model: Any,
    index_dir: Path,
    chunk_ids: list[str],
    questions: list[dict[str, Any]],
) -> dict[str, Any]:
    save_started = time.perf_counter()
    model.save(index_dir, show_progress=False)
    save_s = time.perf_counter() - save_started
    _write_text_exclusive(
        index_dir / "chunk_ids.jsonl",
        "".join(json.dumps({"chunk_id": value}) + "\n" for value in chunk_ids),
    )
    load_started = time.perf_counter()
    loaded = bm25s_module.BM25.load(
        index_dir,
        mmap=True,
        load_corpus=False,
        show_progress=False,
    )
    load_s = time.perf_counter() - load_started

    from backend.services.sparse_retriever import tokenize

    score_equal = True
    for question in questions:
        tokens = tokenize(question["question"])
        if not np.array_equal(model.get_scores(tokens), loaded.get_scores(tokens)):
            score_equal = False
            break
    files = [path for path in index_dir.rglob("*") if path.is_file()]
    mmap_arrays = {
        key: isinstance(value, np.memmap)
        for key, value in loaded.scores.items()
        if key != "num_docs"
    }
    return {
        "save_s": save_s,
        "mmap_load_s": load_s,
        "score_arrays_equal_after_reload": score_equal,
        "mmap_arrays": mmap_arrays,
        "files": len(files),
        "bytes": sum(path.stat().st_size for path in files),
    }


def _render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# BM25持久化P1等价性探针",
        "",
        f"- 状态：`{payload['status']}`",
        f"- 运行时间：`{payload['finished_at']}`",
        f"- 语料：{payload['inputs']['documents']}篇 / {payload['inputs']['chunks']} chunks",
        f"- 题目：{payload['inputs']['questions']}题",
        f"- BM25S：`{payload['versions']['bm25s']}`（仅P1隔离目录）",
        "",
        "| 候选 | K1 | K5 | K15 | K50 | mmap重载一致 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for variant in payload["variants"]:
        counts = variant["comparison"]["exact_order_questions"]
        lines.append(
            f"| {variant['name']} | {counts['1']}/120 | {counts['5']}/120 | "
            f"{counts['15']}/120 | {counts['50']}/120 | "
            f"{variant['persistence']['score_arrays_equal_after_reload']} |"
        )
    lines.extend(
        [
            "",
            "结论：" + payload["decision"],
            "",
            "边界：本报告没有修改业务代码、requirements或生产数据；完整系统性能对照另由P1基线报告承载。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=HERE / "runs" / "equivalence-20260903-r2",
    )
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"输出目录已存在，禁止覆盖：{output_dir}")
    output_dir.mkdir(parents=True, exist_ok=False)
    work_storage = output_dir / "work" / "storage"
    source_before = {
        "chroma": _tree_fingerprint(SOURCE_STORAGE / "chroma"),
        "metadata": _sha256_file(SOURCE_STORAGE / "metadata.db"),
    }
    shutil.copytree(SOURCE_STORAGE, work_storage)

    started_wall = time.perf_counter()
    started_at = _now()
    blocked: list[dict[str, Any]] = []
    _configure_isolation(work_storage)
    sys.path.insert(0, str(DEPS_DIR))
    sys.path.insert(0, str(PROJECT_ROOT))
    _install_network_guard(blocked, started_wall)

    work_storage_before = {
        "chroma": _tree_fingerprint(work_storage / "chroma"),
        "metadata": _sha256_file(work_storage / "metadata.db"),
    }
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    questions = dataset["questions"]
    if len(questions) != EXPECTED_QUESTIONS:
        raise RuntimeError(f"题目数量为{len(questions)}，预期{EXPECTED_QUESTIONS}")

    from backend.services import document_store, vector_store
    from backend.services.sparse_retriever import tokenize
    import bm25s

    documents = len(document_store.list_documents(status="ready"))
    chunks = vector_store.get_all_chunks()
    if documents != EXPECTED_DOCUMENTS or len(chunks) != EXPECTED_CHUNKS:
        raise RuntimeError(
            f"隔离库数量不符：documents={documents}, chunks={len(chunks)}"
        )
    chunk_ids = [str(row["chunk_id"]) for row in chunks]
    if len(set(chunk_ids)) != len(chunk_ids):
        raise RuntimeError("chunk_id不唯一")

    process = psutil.Process(os.getpid())
    rss_before_tokens = process.memory_info().rss
    token_started = time.perf_counter()
    corpus_tokens = [tokenize(str(row["content"])) for row in chunks]
    tokenize_s = time.perf_counter() - token_started
    rss_after_tokens = process.memory_info().rss

    oracle_started = time.perf_counter()
    oracle = BM25Okapi(corpus_tokens, k1=K1, b=B, epsilon=EPSILON)
    oracle_build_s = time.perf_counter() - oracle_started
    average_idf = _rank_bm25_average_idf(corpus_tokens)
    if not math.isclose(average_idf, oracle.average_idf, rel_tol=0, abs_tol=1e-10):
        raise RuntimeError("独立计算的average_idf与rank_bm25不一致")
    average_idf = oracle.average_idf

    variants = []
    for name in ("stock", "rank_bm25_compat"):
        model, build_s = _build_bm25s(
            bm25s,
            corpus_tokens,
            variant=name,
            average_idf=average_idf,
        )
        comparison = _compare_variant(
            name=name,
            model=model,
            oracle=oracle,
            questions=questions,
            chunk_ids=chunk_ids,
        )
        persistence = _save_and_reload(
            bm25s,
            model=model,
            index_dir=output_dir / "indices" / name,
            chunk_ids=chunk_ids,
            questions=questions,
        )
        variants.append(
            {
                "name": name,
                "build_s": build_s,
                "comparison": comparison,
                "persistence": persistence,
            }
        )

    compat = next(row for row in variants if row["name"] == "rank_bm25_compat")
    persisted = all(compat["persistence"]["mmap_arrays"].values())
    passed = (
        compat["comparison"]["ordered_equivalent"]
        and compat["persistence"]["score_arrays_equal_after_reload"]
        and persisted
        and not blocked
    )
    source_after = {
        "chroma": _tree_fingerprint(SOURCE_STORAGE / "chroma"),
        "metadata": _sha256_file(SOURCE_STORAGE / "metadata.db"),
    }
    if source_before != source_after:
        raise RuntimeError("P1探针期间只读来源Chroma或metadata发生变化")
    work_storage_after = {
        "chroma": _tree_fingerprint(work_storage / "chroma"),
        "metadata": _sha256_file(work_storage / "metadata.db"),
    }

    payload = {
        "schema_version": 1,
        "status": "passed" if passed else "blocked",
        "decision": (
            "兼容IDF适配后的BM25S通过120题全部TopK有序等价与mmap重载门禁，可继续P2。"
            if passed
            else "兼容候选未通过P1门禁，停止进入P2并检查差异。"
        ),
        "started_at": started_at,
        "finished_at": _now(),
        "wall_s": time.perf_counter() - started_wall,
        "versions": {
            "python": sys.version,
            "numpy": np.__version__,
            "bm25s": bm25s.__version__,
        },
        "inputs": {
            "dataset": str(DATASET_PATH.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "dataset_sha256": _sha256_file(DATASET_PATH),
            "documents": documents,
            "chunks": len(chunks),
            "questions": len(questions),
            "chunk_ids_sha256": hashlib.sha256(
                "\n".join(chunk_ids).encode("utf-8")
            ).hexdigest(),
        },
        "parameters": {"k1": K1, "b": B, "epsilon": EPSILON, "k": K_VALUES},
        "oracle": {
            "implementation": "rank_bm25.BM25Okapi",
            "tokenize_s": tokenize_s,
            "build_s": oracle_build_s,
            "average_idf": oracle.average_idf,
            "rss_before_tokens_bytes": rss_before_tokens,
            "rss_after_tokens_bytes": rss_after_tokens,
        },
        "variants": variants,
        "network_blocked_events": blocked,
        "source_storage_unchanged": source_before == source_after,
        "source_storage_fingerprint": source_after,
        "work_storage_changed_by_chroma_client": work_storage_before != work_storage_after,
        "work_storage_fingerprint_before": work_storage_before,
        "work_storage_fingerprint_after": work_storage_after,
        "prior_attempt": {
            "status": "aborted",
            "reason": "directly opening the frozen evaluation Chroma copy changed its internal storage fingerprint",
            "production_data_accessed": False,
        },
    }
    _write_json_exclusive(output_dir / "result.json", payload)
    _write_text_exclusive(output_dir / "report.md", _render_report(payload))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
