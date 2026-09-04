"""P4 controlled benchmark for legacy in-memory BM25 and persistent mmap BM25.

Each backend runs in three independent processes.  A worker performs one cold
query and then the same ordered 120-question suite as warm queries.  Production
storage and the persistent generation are read-only; every Chroma worker uses
an isolated copy and LLM generation/network access stay disabled.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time
from typing import Any


HERE = Path(__file__).resolve().parent
V31_ROOT = HERE.parent
PROJECT_ROOT = V31_ROOT.parents[2]
SOURCE_STORAGE = V31_ROOT / "tuning" / "v35-a-baseline-20260829" / "storage"
DATASET_PATH = V31_ROOT / "questions_v3_120q.json"
INDEX_ROOT = V31_ROOT / "bm25_persistence_p2" / "sparse-index"
MANIFEST_PATH = (
    INDEX_ROOT / "generations" / "p2-1000-20260903-r5" / "manifest.json"
)
P3_RESULT_PATH = V31_ROOT / "bm25_persistence_p3" / "result.json"
CORPUS_ID = "crud-rag-1000-v1"
EXPECTED_DOCUMENTS = 1000
EXPECTED_CHUNKS = 2914
EXPECTED_QUESTIONS = 120
RUN_COUNT = 3
BACKENDS = ("rank_bm25_memory", "bm25s_mmap")
CODE_EVIDENCE = (
    Path(__file__).resolve(),
    PROJECT_ROOT / "backend/config.py",
    PROJECT_ROOT / "backend/services/bm25s_store.py",
    PROJECT_ROOT / "backend/services/sparse_index.py",
    PROJECT_ROOT / "backend/services/sparse_retriever.py",
    PROJECT_ROOT / "backend/services/sparse_tokenizer.py",
    PROJECT_ROOT / "backend/services/retriever.py",
    PROJECT_ROOT / "backend/services/fusion.py",
    PROJECT_ROOT / "backend/services/reranker.py",
    PROJECT_ROOT / "backend/services/embedding.py",
    PROJECT_ROOT / "backend/services/vector_store.py",
    PROJECT_ROOT / "eval/adapters.py",
    PROJECT_ROOT / "eval/retrieval_sweep.py",
    V31_ROOT / "tuning_runner.py",
    DATASET_PATH,
    MANIFEST_PATH,
)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _canonical_sha(payload: Any) -> str:
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _tree_fingerprint(root: Path) -> dict[str, Any]:
    rows = [
        {
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    ]
    return {
        "files": len(rows),
        "bytes": sum(row["bytes"] for row in rows),
        "sha256": _canonical_sha(rows),
    }


def _write_json_exclusive(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _write_text_exclusive(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[round(fraction * (len(ordered) - 1))]


def _stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {
            "count": 0,
            "sum": 0.0,
            "mean": None,
            "p50": None,
            "p95": None,
            "max": None,
        }
    return {
        "count": len(values),
        "sum": sum(values),
        "mean": statistics.mean(values),
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "max": max(values),
    }


def _delta(after: Any, before: Any, field: str) -> int | None:
    left = getattr(after, field, None)
    right = getattr(before, field, None)
    return None if left is None or right is None else int(left - right)


def _memory_snapshot(process: Any) -> dict[str, int | None]:
    memory = process.memory_info()
    try:
        full = process.memory_full_info()
    except (AttributeError, OSError):
        full = memory
    return {
        "rss_bytes": int(memory.rss),
        "vms_bytes": int(memory.vms),
        "private_bytes": (
            int(getattr(full, "private"))
            if getattr(full, "private", None) is not None
            else None
        ),
    }


def _index_maps(process: Any) -> dict[str, int]:
    root = str(INDEX_ROOT.resolve()).lower()
    try:
        rows = [
            row
            for row in process.memory_maps(grouped=False)
            if str(getattr(row, "path", "")).lower().startswith(root)
        ]
    except (AttributeError, OSError):
        rows = []
    return {
        "count": len(rows),
        "rss_bytes": sum(int(getattr(row, "rss", 0) or 0) for row in rows),
        "private_bytes": sum(
            int(getattr(row, "private", 0) or 0) for row in rows
        ),
    }


class _ProcessSampler:
    def __init__(self, process: Any, psutil_module: Any, interval_s: float = 0.10) -> None:
        self.process = process
        self.psutil = psutil_module
        self.interval_s = interval_s
        self.samples: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        self.process.cpu_percent(interval=None)
        self.psutil.cpu_percent(interval=None)
        started = time.perf_counter()
        while not self._stop.wait(self.interval_s):
            try:
                virtual = self.psutil.virtual_memory()
                self.samples.append(
                    {
                        "at_s": time.perf_counter() - started,
                        "process_cpu_percent": float(
                            self.process.cpu_percent(interval=None)
                        ),
                        "system_cpu_percent": float(
                            self.psutil.cpu_percent(interval=None)
                        ),
                        "system_memory_percent": float(virtual.percent),
                        "system_available_bytes": int(virtual.available),
                        **_memory_snapshot(self.process),
                    }
                )
            except (OSError, RuntimeError):
                return

    def __enter__(self) -> "_ProcessSampler":
        self._thread.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self._stop.set()
        self._thread.join(timeout=2)


def _install_network_guard(events: list[dict[str, Any]], started: float) -> None:
    def deny(event: str, args: tuple[Any, ...]) -> None:
        if event == "socket.connect" and args[0].family in (
            socket.AF_INET,
            socket.AF_INET6,
        ):
            events.append({"event": event, "at_s": time.perf_counter() - started})
            raise PermissionError("P4 benchmark forbids outbound network access")

    sys.addaudithook(deny)


def _stage_candidates(trace: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return next(
        stage["candidates"] for stage in trace["stages"] if stage["name"] == name
    )


def _stage_stats(rows: list[dict[str, Any]]) -> dict[str, dict[str, float | None]]:
    values: dict[str, list[float]] = {}
    for row in rows:
        for key, value in (row.get("trace") or {}).get("timings_ms", {}).items():
            if isinstance(value, (int, float)):
                values.setdefault(key, []).append(float(value))
    return {key: _stats(items) for key, items in sorted(values.items())}


def _compact_quality(candidate: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    quality = {field: candidate.get(field) for field in fields}
    if quality.get("answerable_count") is None:
        quality["answerable_count"] = int(quality["question_count"]) - int(
            quality["unanswerable_count"]
        )
    return quality


def _trace_diagnostics(trace: dict[str, Any]) -> dict[str, Any]:
    names = (
        "sparse_backend",
        "sparse_index_generation",
        "sparse_manifest_hash",
        "sparse_index_status",
        "sparse_fallback_reason",
        "sparse_index_load_ms",
        "sparse_contract_version",
    )
    return {name: trace.get(name) for name in names}


def _cuda_snapshot(torch_module: Any) -> dict[str, Any]:
    available = bool(torch_module.cuda.is_available())
    return {
        "available": available,
        "device": torch_module.cuda.get_device_name(0) if available else None,
        "allocated_bytes": int(torch_module.cuda.memory_allocated()) if available else 0,
        "reserved_bytes": int(torch_module.cuda.memory_reserved()) if available else 0,
        "peak_allocated_bytes": int(torch_module.cuda.max_memory_allocated()) if available else 0,
        "peak_reserved_bytes": int(torch_module.cuda.max_memory_reserved()) if available else 0,
    }


def _worker(run_dir: Path, backend: str) -> None:
    process_started = time.perf_counter()
    storage = run_dir / "work" / "storage"
    if backend not in BACKENDS:
        raise ValueError(f"unknown backend: {backend}")
    if not (storage / "chroma").is_dir() or not (storage / "metadata.db").is_file():
        raise FileNotFoundError(f"incomplete isolated storage: {storage}")
    (storage / "uploads").mkdir(exist_ok=True)

    sys.path.insert(0, str(PROJECT_ROOT))
    from eval.crud_rag_1000.v3_1 import tuning_runner

    os.environ.update(tuning_runner.PINNED_ENV)
    os.environ.update(
        {
            "CHROMA_PERSIST_DIR": str((storage / "chroma").resolve()),
            "METADATA_DB_PATH": str((storage / "metadata.db").resolve()),
            "UPLOAD_DIR": str((storage / "uploads").resolve()),
            "SPARSE_BACKEND": backend,
            "SPARSE_INDEX_ROOT": str(INDEX_ROOT.resolve()),
            "SPARSE_INDEX_CORPUS_ID": CORPUS_ID,
            "CHUNK_STRATEGY": "fixed",
            "CHUNK_SIZE": "500",
            "CHUNK_OVERLAP": "50",
            "CHUNK_MAX_TOKENS": "480",
            "BM25_K1": "1.5",
            "BM25_B": "0.75",
        }
    )
    blocked_network: list[dict[str, Any]] = []
    _install_network_guard(blocked_network, process_started)

    import psutil
    import torch
    from backend.services import sparse_retriever, vector_store
    from eval import adapters
    from eval.retrieval_sweep import build_candidate_result

    adapters.assert_isolation()
    dataset = tuning_runner._dataset()
    questions = dataset["questions"]
    if len(questions) != EXPECTED_QUESTIONS:
        raise RuntimeError(f"question count {len(questions)}, expected {EXPECTED_QUESTIONS}")
    runtime_config = tuning_runner._runtime_config(tuning_runner.BASELINE_CONFIG)

    full_scan_calls = 0
    memory_build_calls = 0
    if backend == "bm25s_mmap":
        def reject_full_scan():
            nonlocal full_scan_calls
            full_scan_calls += 1
            raise AssertionError("mmap path called get_all_chunks")

        def reject_memory_build(*_args: Any, **_kwargs: Any):
            nonlocal memory_build_calls
            memory_build_calls += 1
            raise AssertionError("mmap path built BM25Okapi")

        vector_store.get_all_chunks = reject_full_scan
        sparse_retriever.BM25Okapi = reject_memory_build

    process = psutil.Process()
    before_memory = _memory_snapshot(process)
    before_cpu = process.cpu_times()
    before_io = process.io_counters()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()

    rows: list[dict[str, Any]] = []
    measured_started = time.perf_counter()
    with _ProcessSampler(process, psutil) as sampler:
        cold = adapters.retrieve_only(
            questions[0]["question"],
            include_trace=True,
            runtime_config=runtime_config,
        )
        after_cold_memory = _memory_snapshot(process)
        after_cold_maps = _index_maps(process)
        after_cold_cuda = _cuda_snapshot(torch)
        warm_started = time.perf_counter()
        for index, question in enumerate(questions, 1):
            response = adapters.retrieve_only(
                question["question"],
                include_trace=True,
                runtime_config=runtime_config,
            )
            rows.append(
                {
                    "id": question["id"],
                    "retrieval_s": response["retrieval_s"],
                    "retrieval": response["retrieval"],
                    "trace": response["trace"],
                }
            )
            if index % 10 == 0:
                print(
                    f"{backend}/{run_dir.name}: warm {index}/{len(questions)}",
                    flush=True,
                )
        warm_wall_s = time.perf_counter() - warm_started
    measured_wall_s = time.perf_counter() - measured_started

    after_cpu = process.cpu_times()
    after_io = process.io_counters()
    after_memory = _memory_snapshot(process)
    after_maps = _index_maps(process)
    after_cuda = _cuda_snapshot(torch)

    candidate = build_candidate_result(
        candidate_k=tuning_runner.BASELINE_CONFIG["reranker_candidate_k"],
        questions=questions,
        retrievals={row["id"]: row["retrieval"] for row in rows},
        timings_s=[row["retrieval_s"] for row in rows],
        traces={row["id"]: row["trace"] for row in rows},
    )
    run_config = adapters.get_run_config()
    if not run_config["runtime"]["embedding"]["verified"]:
        raise RuntimeError("embedding device was not verified")
    if not run_config["runtime"]["reranker"]["verified"]:
        raise RuntimeError("reranker device was not verified")

    rows_path = run_dir / "retrieval.jsonl"
    with rows_path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(
                json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            )

    sparse_rows = []
    for question in questions:
        hits = sparse_retriever.search(
            question["question"], top_k=50, k1=1.5, b=0.75
        )
        sparse_rows.append(
            {
                "id": question["id"],
                "hits": [
                    {"chunk_id": hit["chunk_id"], "score": hit["bm25_score"]}
                    for hit in hits
                ],
            }
        )
    sparse_path = run_dir / "sparse_top50.jsonl"
    with sparse_path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in sparse_rows:
            handle.write(
                json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            )

    concurrency = {"executed": backend == "bm25s_mmap", "questions": 0, "exact": 0}
    if backend == "bm25s_mmap":
        subset = list(zip(questions[:24], sparse_rows[:24]))

        def concurrent_query(item: tuple[dict[str, Any], dict[str, Any]]) -> bool:
            question, expected = item
            hits = sparse_retriever.search(
                question["question"], top_k=15, k1=1.5, b=0.75
            )
            return [hit["chunk_id"] for hit in hits] == [
                hit["chunk_id"] for hit in expected["hits"][:15]
            ]

        concurrency_started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(concurrent_query, subset))
        concurrency = {
            "executed": True,
            "workers": 4,
            "questions": len(outcomes),
            "exact": sum(outcomes),
            "wall_s": time.perf_counter() - concurrency_started,
        }

    sparse_retriever.clear_cache()
    after_close_maps = _index_maps(process)
    vector_store._client._system.stop()

    process_cpu_samples = [row["process_cpu_percent"] for row in sampler.samples]
    system_cpu_samples = [row["system_cpu_percent"] for row in sampler.samples]
    system_memory_samples = [row["system_memory_percent"] for row in sampler.samples]
    rss_samples = [row["rss_bytes"] for row in sampler.samples]
    private_samples = [
        row["private_bytes"]
        for row in sampler.samples
        if row["private_bytes"] is not None
    ]
    warm_values = [float(row["retrieval_s"]) for row in rows]
    traces = [cold["trace"], *(row["trace"] for row in rows)]
    diagnostics = [_trace_diagnostics(trace) for trace in traces]
    expected_backend = backend
    if any(row["sparse_backend"] != expected_backend for row in diagnostics):
        raise AssertionError("trace sparse backend mismatch")

    result = {
        "schema_version": "p4-controlled-backend-run-v1",
        "status": "passed",
        "created_at": _now(),
        "backend": backend,
        "run": run_dir.name,
        "scope": {
            "documents": EXPECTED_DOCUMENTS,
            "chunks": EXPECTED_CHUNKS,
            "cold_queries": 1,
            "warm_questions": len(rows),
        },
        "inputs": {
            "dataset": {
                "path": str(DATASET_PATH),
                "sha256": _sha256_file(DATASET_PATH),
            },
            "work_storage_before_open": _tree_fingerprint(storage),
            "index_root": _tree_fingerprint(INDEX_ROOT),
            "code_sha256": {
                str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"): _sha256_file(path)
                for path in CODE_EVIDENCE
            },
        },
        "config": tuning_runner.BASELINE_CONFIG,
        "runtime": run_config,
        "quality": _compact_quality(candidate, tuning_runner.QUALITY_FIELDS),
        "timing": {
            "startup_to_query_ready_s": measured_started - process_started,
            "cold_first_query_s": float(cold["retrieval_s"]),
            "cold_stage_ms": _stage_stats(
                [{"trace": cold["trace"]}]
            ),
            "warm_queries_s": _stats(warm_values),
            "warm_wall_120_s": warm_wall_s,
            "warm_throughput_qps": len(rows) / warm_wall_s,
            "warm_stage_ms": _stage_stats(rows),
            "measured_cold_plus_warm_wall_s": measured_wall_s,
        },
        "sparse": {
            "diagnostics_first": diagnostics[0],
            "load_events": sum(
                1 for row in diagnostics if (row["sparse_index_load_ms"] or 0) > 0
            ),
            "full_corpus_scan_calls_on_mmap": full_scan_calls,
            "bm25_okapi_build_calls_on_mmap": memory_build_calls,
            "top50_artifact": sparse_path.name,
            "top50_sha256": _sha256_file(sparse_path),
            "concurrency": concurrency,
            "maps_after_cold": after_cold_maps,
            "maps_after_warm": after_maps,
            "maps_after_close": after_close_maps,
        },
        "resources": {
            "machine": {
                "platform": platform.platform(),
                "python": sys.version,
                "logical_cpu_count": psutil.cpu_count(logical=True),
                "physical_cpu_count": psutil.cpu_count(logical=False),
                "total_memory_bytes": int(psutil.virtual_memory().total),
            },
            "cpu_time_s": {
                "user": after_cpu.user - before_cpu.user,
                "system": after_cpu.system - before_cpu.system,
                "total": (after_cpu.user - before_cpu.user)
                + (after_cpu.system - before_cpu.system),
            },
            "process_cpu_percent_samples": _stats(process_cpu_samples),
            "system_cpu_percent_samples": _stats(system_cpu_samples),
            "system_memory_percent_samples": _stats(system_memory_samples),
            "memory_before_query": before_memory,
            "memory_after_cold": after_cold_memory,
            "memory_after_warm": after_memory,
            "peak_sampled_rss_bytes": max(
                rss_samples, default=after_memory["rss_bytes"]
            ),
            "peak_sampled_private_bytes": max(
                private_samples, default=after_memory["private_bytes"] or 0
            ),
            "io_delta_bytes": {
                "read": _delta(after_io, before_io, "read_bytes"),
                "write": _delta(after_io, before_io, "write_bytes"),
                "other": _delta(after_io, before_io, "other_bytes"),
            },
            "cuda_after_cold": after_cold_cuda,
            "cuda_after_warm": after_cuda,
            "sample_count": len(sampler.samples),
        },
        "network": {"blocked_events": blocked_network},
        "artifacts": {
            "retrieval_jsonl": rows_path.name,
            "retrieval_jsonl_sha256": _sha256_file(rows_path),
        },
        "worker_wall_s": time.perf_counter() - process_started,
    }
    if blocked_network:
        raise RuntimeError(f"outbound network attempts detected: {blocked_network}")
    if backend == "bm25s_mmap" and (
        full_scan_calls
        or memory_build_calls
        or concurrency["exact"] != concurrency["questions"]
        or after_close_maps["count"] != 0
    ):
        raise AssertionError("persistent sparse safety check failed")
    _write_json_exclusive(run_dir / "result.json", result)


def _run_paths(output: Path, backend: str) -> list[Path]:
    return [output / backend / f"run-{index:02d}" for index in range(1, RUN_COUNT + 1)]


def _aggregate_backend(output: Path, backend: str) -> dict[str, Any]:
    paths = _run_paths(output, backend)
    results = [json.loads((path / "result.json").read_text(encoding="utf-8")) for path in paths]
    rows = [_read_jsonl(path / "retrieval.jsonl") for path in paths]
    warm_values = [float(row["retrieval_s"]) for run_rows in rows for row in run_rows]
    stage_names = sorted(
        {
            name
            for run_rows in rows
            for row in run_rows
            for name in row["trace"]["timings_ms"]
        }
    )
    warm_stage = {
        name: _stats(
            [
                float(row["trace"]["timings_ms"][name])
                for run_rows in rows
                for row in run_rows
            ]
        )
        for name in stage_names
    }
    cold_stage = {
        name: _stats(
            [
                float(result["timing"]["cold_stage_ms"][name]["mean"])
                for result in results
            ]
        )
        for name in stage_names
    }
    resource_fields = {
        "cpu_time_total_s": [row["resources"]["cpu_time_s"]["total"] for row in results],
        "process_cpu_percent_mean": [
            row["resources"]["process_cpu_percent_samples"]["mean"] for row in results
        ],
        "system_cpu_percent_mean": [
            row["resources"]["system_cpu_percent_samples"]["mean"] for row in results
        ],
        "system_memory_percent_mean": [
            row["resources"]["system_memory_percent_samples"]["mean"] for row in results
        ],
        "memory_before_rss_bytes": [
            row["resources"]["memory_before_query"]["rss_bytes"] for row in results
        ],
        "memory_after_cold_rss_bytes": [
            row["resources"]["memory_after_cold"]["rss_bytes"] for row in results
        ],
        "peak_rss_bytes": [row["resources"]["peak_sampled_rss_bytes"] for row in results],
        "peak_private_bytes": [
            row["resources"]["peak_sampled_private_bytes"] for row in results
        ],
        "disk_read_bytes": [row["resources"]["io_delta_bytes"]["read"] for row in results],
        "disk_write_bytes": [row["resources"]["io_delta_bytes"]["write"] for row in results],
        "gpu_peak_allocated_bytes": [
            row["resources"]["cuda_after_warm"]["peak_allocated_bytes"] for row in results
        ],
        "gpu_peak_reserved_bytes": [
            row["resources"]["cuda_after_warm"]["peak_reserved_bytes"] for row in results
        ],
    }
    return {
        "runs": RUN_COUNT,
        "cold_first_query_s": _stats(
            [row["timing"]["cold_first_query_s"] for row in results]
        ),
        "startup_to_query_ready_s": _stats(
            [row["timing"]["startup_to_query_ready_s"] for row in results]
        ),
        "warm_queries_s_all_360": _stats(warm_values),
        "warm_wall_120_s": _stats(
            [row["timing"]["warm_wall_120_s"] for row in results]
        ),
        "warm_throughput_qps": _stats(
            [row["timing"]["warm_throughput_qps"] for row in results]
        ),
        "cold_stage_ms": cold_stage,
        "warm_stage_ms_all_360": warm_stage,
        "resources": {name: _stats(values) for name, values in resource_fields.items()},
        "quality": results[0]["quality"],
        "sparse_load_events": sum(row["sparse"]["load_events"] for row in results),
        "mmap": {
            "after_cold": results[0]["sparse"]["maps_after_cold"],
            "after_warm": results[0]["sparse"]["maps_after_warm"],
            "after_close": results[0]["sparse"]["maps_after_close"],
        },
        "run_results": [
            str((path / "result.json").relative_to(output)).replace("\\", "/")
            for path in paths
        ],
    }


def _ids(row: dict[str, Any], stage: str) -> list[str]:
    if stage == "selected":
        return list(row["trace"]["selected_chunk_ids"])
    return [item["chunk_id"] for item in _stage_candidates(row["trace"], stage)]


def _correctness(output: Path) -> dict[str, Any]:
    references = {
        "retrieval": _read_jsonl(_run_paths(output, "rank_bm25_memory")[0] / "retrieval.jsonl"),
        "sparse": _read_jsonl(_run_paths(output, "rank_bm25_memory")[0] / "sparse_top50.jsonl"),
    }
    comparison_runs = [
        (backend, index, path)
        for backend in BACKENDS
        for index, path in enumerate(_run_paths(output, backend), 1)
        if not (backend == "rank_bm25_memory" and index == 1)
    ]
    stage_exact: dict[str, list[int]] = {
        name: [] for name in ("dense", "bm25", "rrf", "reranker", "selected")
    }
    topk_exact: dict[str, list[int]] = {str(k): [] for k in (1, 5, 15, 50)}
    max_score_delta = 0.0
    for _backend, _index, path in comparison_runs:
        rows = _read_jsonl(path / "retrieval.jsonl")
        sparse = _read_jsonl(path / "sparse_top50.jsonl")
        for stage in stage_exact:
            stage_exact[stage].append(
                sum(
                    _ids(candidate, stage) == _ids(reference, stage)
                    for reference, candidate in zip(references["retrieval"], rows)
                )
            )
        for k in (1, 5, 15, 50):
            topk_exact[str(k)].append(
                sum(
                    [hit["chunk_id"] for hit in candidate["hits"][:k]]
                    == [hit["chunk_id"] for hit in reference["hits"][:k]]
                    for reference, candidate in zip(references["sparse"], sparse)
                )
            )
        for reference, candidate in zip(references["sparse"], sparse):
            for old, new in zip(reference["hits"], candidate["hits"]):
                max_score_delta = max(
                    max_score_delta, abs(float(old["score"]) - float(new["score"]))
                )
    return {
        "reference": "rank_bm25_memory/run-01",
        "comparison_runs": len(comparison_runs),
        "bm25_order_exact_questions_by_k_per_comparison": topk_exact,
        "hybrid_stage_exact_questions_per_comparison": stage_exact,
        "max_bm25_score_delta": max_score_delta,
        "bm25_score_tolerance": 1e-12,
    }


def _metric_comparison(control: float, mmap: float) -> dict[str, float | None]:
    return {
        "control": control,
        "mmap": mmap,
        "delta": mmap - control,
        "change_percent": None if control == 0 else (mmap - control) / control * 100,
    }


def _comparisons(backends: dict[str, Any]) -> dict[str, Any]:
    old = backends["rank_bm25_memory"]
    new = backends["bm25s_mmap"]
    definitions = {
        "cold_first_query_s_mean": (
            old["cold_first_query_s"]["mean"], new["cold_first_query_s"]["mean"]
        ),
        "warm_query_ms_mean": (
            old["warm_queries_s_all_360"]["mean"] * 1000,
            new["warm_queries_s_all_360"]["mean"] * 1000,
        ),
        "warm_query_ms_p95": (
            old["warm_queries_s_all_360"]["p95"] * 1000,
            new["warm_queries_s_all_360"]["p95"] * 1000,
        ),
        "warm_wall_120_s_mean": (
            old["warm_wall_120_s"]["mean"], new["warm_wall_120_s"]["mean"]
        ),
        "warm_throughput_qps_mean": (
            old["warm_throughput_qps"]["mean"], new["warm_throughput_qps"]["mean"]
        ),
        "sparse_cold_ms_mean": (
            old["cold_stage_ms"]["sparse_retrieval"]["mean"],
            new["cold_stage_ms"]["sparse_retrieval"]["mean"],
        ),
        "sparse_warm_ms_mean": (
            old["warm_stage_ms_all_360"]["sparse_retrieval"]["mean"],
            new["warm_stage_ms_all_360"]["sparse_retrieval"]["mean"],
        ),
        "sparse_warm_ms_p95": (
            old["warm_stage_ms_all_360"]["sparse_retrieval"]["p95"],
            new["warm_stage_ms_all_360"]["sparse_retrieval"]["p95"],
        ),
        "cpu_time_s_mean": (
            old["resources"]["cpu_time_total_s"]["mean"],
            new["resources"]["cpu_time_total_s"]["mean"],
        ),
        "peak_rss_bytes_mean": (
            old["resources"]["peak_rss_bytes"]["mean"],
            new["resources"]["peak_rss_bytes"]["mean"],
        ),
        "peak_private_bytes_mean": (
            old["resources"]["peak_private_bytes"]["mean"],
            new["resources"]["peak_private_bytes"]["mean"],
        ),
        "disk_read_bytes_mean": (
            old["resources"]["disk_read_bytes"]["mean"],
            new["resources"]["disk_read_bytes"]["mean"],
        ),
        "disk_write_bytes_mean": (
            old["resources"]["disk_write_bytes"]["mean"],
            new["resources"]["disk_write_bytes"]["mean"],
        ),
        "gpu_peak_allocated_bytes_mean": (
            old["resources"]["gpu_peak_allocated_bytes"]["mean"],
            new["resources"]["gpu_peak_allocated_bytes"]["mean"],
        ),
        "gpu_peak_reserved_bytes_mean": (
            old["resources"]["gpu_peak_reserved_bytes"]["mean"],
            new["resources"]["gpu_peak_reserved_bytes"]["mean"],
        ),
    }
    return {
        name: _metric_comparison(float(control), float(mmap))
        for name, (control, mmap) in definitions.items()
    }


def _report(summary: dict[str, Any]) -> str:
    comparisons = summary["comparison"]
    correctness = summary["correctness"]
    lines = [
        "# BM25 持久化 P4 同期对照", "",
        f"- 状态：{summary['status']}",
        "- 范围：1000篇 / 2914 chunks / 120题；旧、新后端各3个独立进程。",
        "- 每进程：1次冷查询后，按固定顺序完整执行120次暖态Hybrid检索；不调用LLM。",
        "- P1为历史前态；本报告的变化率全部使用本轮同期旧后端control。", "",
        "## 正确性", "",
    ]
    for k, counts in correctness["bm25_order_exact_questions_by_k_per_comparison"].items():
        lines.append(f"- BM25 Top{k}：{min(counts)}/120（5个非参考run均一致）")
    for stage, counts in correctness["hybrid_stage_exact_questions_per_comparison"].items():
        lines.append(f"- {stage}：{min(counts)}/120")
    lines.extend([
        f"- 最大BM25分数差：{correctness['max_bm25_score_delta']:.3e}", "",
        "独立GPU进程中的Dense/重排顺序存在重复运行波动，且同期旧后端自身也出现；",
        "因此它只作为观测值，不作为Sparse回归门禁。同一Dense候选下的P3回放保持120/120一致。", "",
        "## 主要性能", "",
        "| 指标 | 同期旧后端 | mmap后端 | 变化 |",
        "|---|---:|---:|---:|",
    ])
    display = (
        ("冷查询均值", "cold_first_query_s_mean", "s"),
        ("暖态单题均值", "warm_query_ms_mean", "ms"),
        ("暖态单题P95", "warm_query_ms_p95", "ms"),
        ("120题wall均值", "warm_wall_120_s_mean", "s"),
        ("吞吐均值", "warm_throughput_qps_mean", "q/s"),
        ("Sparse冷查询均值", "sparse_cold_ms_mean", "ms"),
        ("Sparse暖态均值", "sparse_warm_ms_mean", "ms"),
        ("Sparse暖态P95", "sparse_warm_ms_p95", "ms"),
    )
    for label, key, unit in display:
        row = comparisons[key]
        lines.append(
            f"| {label} | {row['control']:.3f} {unit} | {row['mmap']:.3f} {unit} | "
            f"{row['change_percent']:+.2f}% |"
        )
    lines.extend(["", "## 资源", ""])
    for key in (
        "cpu_time_s_mean", "peak_rss_bytes_mean", "peak_private_bytes_mean",
        "disk_read_bytes_mean", "disk_write_bytes_mean",
        "gpu_peak_allocated_bytes_mean", "gpu_peak_reserved_bytes_mean",
    ):
        row = comparisons[key]
        lines.append(
            f"- {key}: control={row['control']:.3f}, mmap={row['mmap']:.3f}, "
            f"change={row['change_percent']:+.2f}%"
        )
    lines.extend(["", "## 门禁", ""])
    lines.extend(f"- {name}: {value}" for name, value in summary["checks"].items())
    lines.extend([
        "", "## 边界", "",
        "本结果只适用于当前1000篇固定语料、120题、当前硬件与固定模型。",
        "不外推1万篇或生产容量；是否采用由P5评审决定，生产默认配置未切换。", "",
    ])
    return "\n".join(lines)


def _parent(output: Path) -> None:
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    if not SOURCE_STORAGE.is_dir() or not DATASET_PATH.is_file():
        raise FileNotFoundError("frozen P4 source inputs are missing")
    if (
        not INDEX_ROOT.is_dir()
        or not MANIFEST_PATH.is_file()
        or not P3_RESULT_PATH.is_file()
    ):
        raise FileNotFoundError("P2 sparse generation is missing")
    output.mkdir(parents=True)
    source_before = _tree_fingerprint(SOURCE_STORAGE)
    index_before = _tree_fingerprint(INDEX_ROOT)

    schedule = (
        ("rank_bm25_memory", 1), ("bm25s_mmap", 1),
        ("bm25s_mmap", 2), ("rank_bm25_memory", 2),
        ("rank_bm25_memory", 3), ("bm25s_mmap", 3),
    )
    copy_times: list[float] = []
    for backend, index in schedule:
        run_dir = output / backend / f"run-{index:02d}"
        storage = run_dir / "work" / "storage"
        copied = time.perf_counter()
        shutil.copytree(SOURCE_STORAGE, storage)
        copy_times.append(time.perf_counter() - copied)
        print(f"starting {backend} run {index}/{RUN_COUNT}", flush=True)
        subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--worker",
                "--backend",
                backend,
                "--run-dir",
                str(run_dir),
            ],
            cwd=PROJECT_ROOT,
            check=True,
        )
        shutil.rmtree(run_dir / "work")

    source_after = _tree_fingerprint(SOURCE_STORAGE)
    index_after = _tree_fingerprint(INDEX_ROOT)
    backend_results = {
        backend: _aggregate_backend(output, backend) for backend in BACKENDS
    }
    correctness = _correctness(output)
    comparison = _comparisons(backend_results)
    run_results = [
        json.loads((path / "result.json").read_text(encoding="utf-8"))
        for backend in BACKENDS
        for path in _run_paths(output, backend)
    ]
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    bm25_exact = all(
        min(counts) == EXPECTED_QUESTIONS
        for counts in correctness[
            "bm25_order_exact_questions_by_k_per_comparison"
        ].values()
    )
    p3_result = json.loads(P3_RESULT_PATH.read_text(encoding="utf-8"))
    quality_values = [row["quality"] for row in run_results]
    mmap_runs = [row for row in run_results if row["backend"] == "bm25s_mmap"]
    control_runs = [row for row in run_results if row["backend"] == "rank_bm25_memory"]
    checks = {
        "six_runs_passed": len(run_results) == 6 and all(row["status"] == "passed" for row in run_results),
        "scope_exact": all(
            row["scope"] == {
                "documents": EXPECTED_DOCUMENTS,
                "chunks": EXPECTED_CHUNKS,
                "cold_queries": 1,
                "warm_questions": EXPECTED_QUESTIONS,
            }
            for row in run_results
        ),
        "dataset_and_code_frozen": len(
            {_canonical_sha(row["inputs"]["code_sha256"]) for row in run_results}
        ) == 1,
        "quality_identical": all(value == quality_values[0] for value in quality_values[1:]),
        "bm25_order_exact": bm25_exact,
        "same_dense_replay_exact": all(
            p3_result[name] == EXPECTED_QUESTIONS
            for name in (
                "sparse_top15_order_exact",
                "rrf_order_exact",
                "reranker_input_exact",
                "final_top5_replay_exact",
            )
        ),
        "bm25_scores_within_tolerance": correctness["max_bm25_score_delta"] <= 1e-12,
        "source_storage_unchanged": source_before == source_after,
        "sparse_index_unchanged": index_before == index_after,
        "network_clean": all(not row["network"]["blocked_events"] for row in run_results),
        "gpu_verified": all(
            row["runtime"]["runtime"][component]["verified"]
            for row in run_results
            for component in ("embedding", "reranker")
        ),
        "mmap_no_scan_or_memory_build": all(
            row["sparse"]["full_corpus_scan_calls_on_mmap"] == 0
            and row["sparse"]["bm25_okapi_build_calls_on_mmap"] == 0
            for row in mmap_runs
        ),
        "mmap_loaded_once_per_process": all(row["sparse"]["load_events"] == 1 for row in mmap_runs),
        "concurrent_reads_exact": all(
            row["sparse"]["concurrency"]["exact"]
            == row["sparse"]["concurrency"]["questions"]
            == 24
            for row in mmap_runs
        ),
        "mmap_handles_released": all(
            row["sparse"]["maps_after_close"]["count"] == 0 for row in mmap_runs
        ),
        "legacy_rollback_path_passed": len(control_runs) == RUN_COUNT,
        "warm_sparse_p95_gate": comparison["sparse_warm_ms_p95"]["mmap"]
        <= comparison["sparse_warm_ms_p95"]["control"] * 1.10,
        "gpu_not_increased": comparison["gpu_peak_allocated_bytes_mean"]["mmap"]
        <= comparison["gpu_peak_allocated_bytes_mean"]["control"],
    }
    summary = {
        "schema_version": "p4-controlled-comparison-v1",
        "status": "passed" if all(checks.values()) else "failed",
        "created_at": _now(),
        "scope": {
            "documents": EXPECTED_DOCUMENTS,
            "chunks": EXPECTED_CHUNKS,
            "questions": EXPECTED_QUESTIONS,
            "cold_queries_per_process": 1,
            "warm_queries_per_process": EXPECTED_QUESTIONS,
            "processes_per_backend": RUN_COUNT,
            "llm_generation": False,
        },
        "schedule": [f"{backend}/run-{index:02d}" for backend, index in schedule],
        "inputs": {
            "dataset_sha256": _sha256_file(DATASET_PATH),
            "source_storage_before": source_before,
            "source_storage_after": source_after,
            "sparse_index_before": index_before,
            "sparse_index_after": index_after,
            "generation_id": manifest["generation_id"],
            "manifest_sha256": _sha256_file(MANIFEST_PATH),
            "index_bytes": manifest["index_bytes"],
            "offline_build": manifest["build"],
        },
        "copy_storage_s": _stats(copy_times),
        "backends": backend_results,
        "correctness": correctness,
        "comparison": comparison,
        "checks": checks,
        "historical_p1_note": (
            "P1 used one cold plus 119 warm queries per process and is retained as historical evidence; "
            "P4 changes use the contemporaneous control above."
        ),
        "independent_process_variation_note": (
            "Dense/RRF/reranker/final ordering varied across independent GPU runs, "
            "including control-to-control repeats. BM25 ordering and all quality metrics "
            "were exact; P3 same-Dense replay is the causal sparse-backend gate."
        ),
        "p3_same_dense_replay": {
            "path": str(P3_RESULT_PATH.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "sha256": _sha256_file(P3_RESULT_PATH),
            "result": p3_result,
        },
        "decision_scope": "P4 measurement only; adoption is P5 and production switching is P6",
    }
    _write_json_exclusive(output / "summary.json", summary)
    _write_text_exclusive(output / "report.md", _report(summary))
    print(json.dumps({"status": summary["status"], "output": str(output)}, ensure_ascii=False), flush=True)
    if summary["status"] != "passed":
        raise RuntimeError(f"P4 gate failed: {checks}")


def _reaggregate(output: Path) -> None:
    """Write an additive v2 summary from immutable completed r1 run artifacts."""
    original_path = output / "summary.json"
    if not original_path.is_file():
        raise FileNotFoundError(f"missing original P4 summary: {original_path}")
    target = output / "summary-v2.json"
    report = output / "report-v2.md"
    if target.exists() or report.exists():
        raise FileExistsError("P4 v2 summary already exists")
    original = json.loads(original_path.read_text(encoding="utf-8"))
    corrected = dict(original)
    corrected["schema_version"] = "p4-controlled-comparison-v2"
    corrected["created_at"] = _now()
    corrected["supersedes"] = "summary.json"
    corrected["correction"] = (
        "原汇总错误地要求独立GPU进程的Dense/RRF/reranker/final逐题顺序完全相同；"
        "同期control自身也有相同波动。v2保留这些观测值，但以六轮BM25 K1/5/15/50、"
        "质量指标和P3同Dense候选回放作为Sparse因果门禁。原始run文件未修改。"
    )
    p3_result = json.loads(P3_RESULT_PATH.read_text(encoding="utf-8"))
    bm25_exact = all(
        min(counts) == EXPECTED_QUESTIONS
        for counts in corrected["correctness"][
            "bm25_order_exact_questions_by_k_per_comparison"
        ].values()
    )
    checks = dict(corrected["checks"])
    checks.pop("all_ordered_results_exact", None)
    checks["bm25_order_exact"] = bm25_exact
    checks["same_dense_replay_exact"] = all(
        p3_result[name] == EXPECTED_QUESTIONS
        for name in (
            "sparse_top15_order_exact",
            "rrf_order_exact",
            "reranker_input_exact",
            "final_top5_replay_exact",
        )
    )
    corrected["checks"] = checks
    corrected["status"] = "passed" if all(checks.values()) else "failed"
    corrected["independent_process_variation_note"] = (
        "Dense/RRF/reranker/final ordering varied across independent GPU runs, "
        "including control-to-control repeats. BM25 ordering and all quality metrics "
        "were exact; P3 same-Dense replay is the causal sparse-backend gate."
    )
    corrected["p3_same_dense_replay"] = {
        "path": str(P3_RESULT_PATH.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "sha256": _sha256_file(P3_RESULT_PATH),
        "result": p3_result,
    }
    _write_json_exclusive(target, corrected)
    _write_text_exclusive(report, _report(corrected))
    print(
        json.dumps(
            {"status": corrected["status"], "summary": str(target)},
            ensure_ascii=False,
        ),
        flush=True,
    )
    if corrected["status"] != "passed":
        raise RuntimeError(f"P4 v2 gate failed: {checks}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=HERE / "runs" / "p4-20260903-r1"
    )
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--backend", choices=BACKENDS)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--reaggregate", type=Path)
    args = parser.parse_args()
    if args.reaggregate is not None:
        _reaggregate(args.reaggregate.resolve())
    elif args.worker:
        if args.backend is None or args.run_dir is None:
            parser.error("--worker requires --backend and --run-dir")
        _worker(args.run_dir.resolve(), args.backend)
    else:
        _parent(args.output.resolve())


if __name__ == "__main__":
    main()
