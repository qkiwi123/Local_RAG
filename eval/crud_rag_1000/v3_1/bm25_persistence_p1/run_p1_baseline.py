"""P1 pre-optimization baseline: three isolated cold processes over 120 queries.

The parent process copies the frozen evaluation storage for every run.  Workers
then execute the real hybrid retriever without LLM calls and capture quality,
stage latency, process resource use, CUDA peaks, and immutable input evidence.
No production path or application source file is modified.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
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
EXPECTED_DOCUMENTS = 1000
EXPECTED_CHUNKS = 2914
EXPECTED_QUESTIONS = 120
RUN_COUNT = 3

CODE_EVIDENCE = (
    PROJECT_ROOT / "backend/services/sparse_retriever.py",
    PROJECT_ROOT / "backend/services/retriever.py",
    PROJECT_ROOT / "backend/services/fusion.py",
    PROJECT_ROOT / "backend/services/reranker.py",
    PROJECT_ROOT / "backend/services/embedding.py",
    PROJECT_ROOT / "backend/services/vector_store.py",
    PROJECT_ROOT / "eval/adapters.py",
    PROJECT_ROOT / "eval/retrieval_sweep.py",
    V31_ROOT / "tuning_runner.py",
    DATASET_PATH,
)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _canonical_sha(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _tree_fingerprint(root: Path) -> dict[str, Any]:
    rows = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
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


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[round(fraction * (len(ordered) - 1))]


def _stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"count": 0, "sum": 0.0, "mean": None, "p50": None,
                "p95": None, "max": None}
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
            if getattr(full, "private", None) is not None else None
        ),
    }


class _ProcessSampler:
    def __init__(self, process: Any, interval_s: float = 0.10) -> None:
        self.process = process
        self.interval_s = interval_s
        self.samples: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        self.process.cpu_percent(interval=None)
        started = time.perf_counter()
        while not self._stop.wait(self.interval_s):
            try:
                self.samples.append(
                    {
                        "at_s": time.perf_counter() - started,
                        "cpu_percent": float(self.process.cpu_percent(interval=None)),
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
        if event == "socket.connect" and args[0].family in (socket.AF_INET, socket.AF_INET6):
            events.append({"event": event, "at_s": time.perf_counter() - started})
            raise PermissionError("P1基线禁止出站网络")

    sys.addaudithook(deny)


def _stage_stats(rows: list[dict[str, Any]]) -> dict[str, dict[str, float | None]]:
    values: dict[str, list[float]] = {}
    for row in rows:
        for key, value in (row.get("trace") or {}).get("timings_ms", {}).items():
            if isinstance(value, (int, float)):
                values.setdefault(key, []).append(float(value))
    return {key: _stats(items) for key, items in sorted(values.items())}


def _compact_quality(candidate: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: candidate.get(field) for field in fields}


def _worker(run_dir: Path) -> None:
    process_started = time.perf_counter()
    storage = run_dir / "work" / "storage"
    if not (storage / "chroma").is_dir() or not (storage / "metadata.db").is_file():
        raise FileNotFoundError(f"工作存储不完整：{storage}")
    (storage / "uploads").mkdir(exist_ok=True)

    sys.path.insert(0, str(PROJECT_ROOT))
    from eval.crud_rag_1000.v3_1 import tuning_runner

    os.environ.update(tuning_runner.PINNED_ENV)
    os.environ.update(
        {
            "CHROMA_PERSIST_DIR": str((storage / "chroma").resolve()),
            "METADATA_DB_PATH": str((storage / "metadata.db").resolve()),
            "UPLOAD_DIR": str((storage / "uploads").resolve()),
        }
    )
    blocked_network: list[dict[str, Any]] = []
    _install_network_guard(blocked_network, process_started)

    import psutil
    import torch
    from eval import adapters
    from eval.retrieval_sweep import build_candidate_result

    adapters.assert_isolation()
    dataset = tuning_runner._dataset()
    questions = dataset["questions"]
    if len(questions) != EXPECTED_QUESTIONS:
        raise RuntimeError(f"题目数 {len(questions)}，预期 {EXPECTED_QUESTIONS}")
    runtime_config = tuning_runner._runtime_config(tuning_runner.BASELINE_CONFIG)

    process = psutil.Process()
    before_memory = _memory_snapshot(process)
    before_cpu = process.cpu_times()
    before_io = process.io_counters()
    cuda_available = bool(torch.cuda.is_available())
    if cuda_available:
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()

    rows: list[dict[str, Any]] = []
    retrieval_started = time.perf_counter()
    with _ProcessSampler(process) as sampler:
        for index, question in enumerate(questions, 1):
            response = adapters.retrieve_only(
                question["question"],
                include_trace=True,
                runtime_config=runtime_config,
            )
            row = {
                "id": question["id"],
                "retrieval_s": response["retrieval_s"],
                "retrieval": response["retrieval"],
                "trace": response["trace"],
            }
            rows.append(row)
            print(f"{run_dir.name}: {index}/{len(questions)}", flush=True) if index % 10 == 0 else None
    retrieval_wall_s = time.perf_counter() - retrieval_started

    after_cpu = process.cpu_times()
    after_io = process.io_counters()
    after_memory = _memory_snapshot(process)
    cpu_samples = [row["cpu_percent"] for row in sampler.samples]
    rss_samples = [row["rss_bytes"] for row in sampler.samples]
    private_samples = [row["private_bytes"] for row in sampler.samples if row["private_bytes"] is not None]

    candidate = build_candidate_result(
        candidate_k=tuning_runner.BASELINE_CONFIG["reranker_candidate_k"],
        questions=questions,
        retrievals={row["id"]: row["retrieval"] for row in rows},
        timings_s=[row["retrieval_s"] for row in rows],
        traces={row["id"]: row["trace"] for row in rows},
    )
    run_config = adapters.get_run_config()
    if not run_config["runtime"]["embedding"]["verified"]:
        raise RuntimeError("Embedding实际设备未验证")
    if not run_config["runtime"]["reranker"]["verified"]:
        raise RuntimeError("Reranker实际设备未验证")

    rows_path = run_dir / "retrieval.jsonl"
    with rows_path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    retrieval_values = [float(row["retrieval_s"]) for row in rows]
    result = {
        "schema_version": "p1-old-baseline-v1",
        "status": "passed",
        "created_at": _now(),
        "run": run_dir.name,
        "inputs": {
            "dataset": {"path": str(DATASET_PATH), "sha256": _sha256_file(DATASET_PATH)},
            "documents": EXPECTED_DOCUMENTS,
            "chunks": EXPECTED_CHUNKS,
            "questions": len(questions),
            "work_storage_before_open": _tree_fingerprint(storage),
            "code_sha256": {
                str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"): _sha256_file(path)
                for path in CODE_EVIDENCE
            },
        },
        "config": tuning_runner.BASELINE_CONFIG,
        "runtime": run_config,
        "quality": _compact_quality(candidate, tuning_runner.QUALITY_FIELDS),
        "retrieval": {
            "cold_first_query_s": retrieval_values[0],
            "all_queries_s": _stats(retrieval_values),
            "warm_queries_s": _stats(retrieval_values[1:]),
            "wall_120_queries_s": retrieval_wall_s,
            "stage_all_ms": _stage_stats(rows),
            "stage_warm_ms": _stage_stats(rows[1:]),
        },
        "resources": {
            "logical_cpu_count": psutil.cpu_count(logical=True),
            "cpu_time_s": {
                "user": after_cpu.user - before_cpu.user,
                "system": after_cpu.system - before_cpu.system,
                "total": (after_cpu.user - before_cpu.user) + (after_cpu.system - before_cpu.system),
            },
            "process_cpu_percent_samples": _stats(cpu_samples),
            "memory_before": before_memory,
            "memory_after": after_memory,
            "peak_sampled_rss_bytes": max(rss_samples, default=after_memory["rss_bytes"]),
            "peak_sampled_private_bytes": max(private_samples, default=after_memory["private_bytes"] or 0),
            "io_delta_bytes": {
                "read": _delta(after_io, before_io, "read_bytes"),
                "write": _delta(after_io, before_io, "write_bytes"),
                "other": _delta(after_io, before_io, "other_bytes"),
            },
            "cuda": {
                "available": cuda_available,
                "device": torch.cuda.get_device_name(0) if cuda_available else None,
                "allocated_after_bytes": int(torch.cuda.memory_allocated()) if cuda_available else 0,
                "reserved_after_bytes": int(torch.cuda.memory_reserved()) if cuda_available else 0,
                "peak_allocated_bytes": int(torch.cuda.max_memory_allocated()) if cuda_available else 0,
                "peak_reserved_bytes": int(torch.cuda.max_memory_reserved()) if cuda_available else 0,
            },
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
        raise RuntimeError(f"检测到出站网络尝试：{blocked_network}")
    _write_json_exclusive(run_dir / "result.json", result)


def _same(values: list[Any]) -> bool:
    return all(value == values[0] for value in values[1:])


def _aggregate(output: Path, runs: list[dict[str, Any]], source_before: dict[str, Any],
               source_after: dict[str, Any], copy_times: list[float]) -> dict[str, Any]:
    qualities = [row["quality"] for row in runs]
    code_hashes = [row["inputs"]["code_sha256"] for row in runs]
    dataset_hashes = [row["inputs"]["dataset"]["sha256"] for row in runs]
    metrics = {
        "cold_first_query_s": [row["retrieval"]["cold_first_query_s"] for row in runs],
        "warm_mean_query_s": [row["retrieval"]["warm_queries_s"]["mean"] for row in runs],
        "warm_p95_query_s": [row["retrieval"]["warm_queries_s"]["p95"] for row in runs],
        "wall_120_queries_s": [row["retrieval"]["wall_120_queries_s"] for row in runs],
        "cpu_time_total_s": [row["resources"]["cpu_time_s"]["total"] for row in runs],
        "peak_rss_bytes": [row["resources"]["peak_sampled_rss_bytes"] for row in runs],
        "peak_private_bytes": [row["resources"]["peak_sampled_private_bytes"] for row in runs],
        "disk_read_bytes": [row["resources"]["io_delta_bytes"]["read"] for row in runs],
        "disk_write_bytes": [row["resources"]["io_delta_bytes"]["write"] for row in runs],
        "gpu_peak_allocated_bytes": [row["resources"]["cuda"]["peak_allocated_bytes"] for row in runs],
        "gpu_peak_reserved_bytes": [row["resources"]["cuda"]["peak_reserved_bytes"] for row in runs],
    }
    checks = {
        "run_count": len(runs) == RUN_COUNT,
        "all_runs_passed": all(row["status"] == "passed" for row in runs),
        "quality_identical": _same(qualities),
        "code_identical": _same(code_hashes),
        "dataset_identical": _same(dataset_hashes),
        "source_storage_unchanged": source_before == source_after,
        "network_clean": all(not row["network"]["blocked_events"] for row in runs),
        "gpu_verified": all(
            row["runtime"]["runtime"][component]["verified"]
            for row in runs for component in ("embedding", "reranker")
        ),
    }
    return {
        "schema_version": "p1-old-baseline-summary-v1",
        "status": "passed" if all(checks.values()) else "failed",
        "created_at": _now(),
        "output": str(output),
        "scope": {"documents": EXPECTED_DOCUMENTS, "chunks": EXPECTED_CHUNKS,
                  "questions": EXPECTED_QUESTIONS, "cold_processes": RUN_COUNT},
        "checks": checks,
        "source_storage": {"path": str(SOURCE_STORAGE), "before": source_before,
                           "after": source_after},
        "copy_storage_s": _stats(copy_times),
        "quality": qualities[0],
        "metrics_across_runs": {key: _stats([float(v) for v in values if v is not None])
                                for key, values in metrics.items()},
        "run_results": [str((output / f"run-{index:02d}" / "result.json").relative_to(output))
                        for index in range(1, RUN_COUNT + 1)],
    }


def _report(summary: dict[str, Any]) -> str:
    metrics = summary["metrics_across_runs"]
    quality = summary["quality"]
    mib = 1024 * 1024
    lines = [
        "# P1 优化前基线", "", f"- 状态：{summary['status']}",
        f"- 范围：{summary['scope']['documents']} 篇文档 / {summary['scope']['chunks']} chunks / "
        f"{summary['scope']['questions']} 题 / {summary['scope']['cold_processes']} 个独立冷进程", "",
        "## 质量（3 次完全一致）", "",
    ]
    lines.extend(f"- {key}: {value}" for key, value in quality.items())
    lines.extend(["", "## 性能与资源（3 次运行均值）", ""])
    for key in ("cold_first_query_s", "warm_mean_query_s", "warm_p95_query_s",
                "wall_120_queries_s", "cpu_time_total_s"):
        lines.append(f"- {key}: {metrics[key]['mean']:.6f} s")
    for key in ("peak_rss_bytes", "peak_private_bytes", "gpu_peak_allocated_bytes",
                "gpu_peak_reserved_bytes"):
        lines.append(f"- {key}: {metrics[key]['mean'] / mib:.2f} MiB")
    for key in ("disk_read_bytes", "disk_write_bytes"):
        lines.append(f"- {key}: {metrics[key]['mean'] / mib:.2f} MiB")
    lines.extend(["", "## 完整性检查", ""])
    lines.extend(f"- {key}: {value}" for key, value in summary["checks"].items())
    lines.append("")
    return "\n".join(lines)


def _load_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _reaggregate_v2(output: Path) -> None:
    """Create an additive corrected summary without rewriting raw P1 artifacts."""
    original = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    runs = [
        json.loads((output / f"run-{index:02d}" / "result.json").read_text(encoding="utf-8"))
        for index in range(1, RUN_COUNT + 1)
    ]
    row_sets = [
        _load_rows(output / f"run-{index:02d}" / "retrieval.jsonl")
        for index in range(1, RUN_COUNT + 1)
    ]
    if any(len(rows) != EXPECTED_QUESTIONS for rows in row_sets):
        raise RuntimeError("原始retrieval JSONL题数不符合120题合同")

    quality = dict(original["quality"])
    quality["answerable_count"] = (
        int(quality["question_count"]) - int(quality["unanswerable_count"])
    )
    stage_names = sorted(
        {
            key
            for rows in row_sets
            for row in rows
            for key in (row.get("trace") or {}).get("timings_ms", {})
        }
    )
    first_query_stages = {
        stage: _stats(
            [float((rows[0].get("trace") or {})["timings_ms"][stage]) for rows in row_sets]
        )
        for stage in stage_names
    }
    warm_stage_values = {
        stage: [
            float((row.get("trace") or {})["timings_ms"][stage])
            for rows in row_sets for row in rows[1:]
        ]
        for stage in stage_names
    }
    corrected = {
        **original,
        "schema_version": "p1-old-baseline-summary-v2",
        "created_at": _now(),
        "supersedes": "summary.json",
        "correction": (
            "answerable_count由question_count-unanswerable_count推导；"
            "新增基于三份原始JSONL的首问与357次暖态分阶段聚合。"
        ),
        "quality": quality,
        "first_query_stage_ms_across_runs": first_query_stages,
        "warm_stage_ms_all_357_queries": {
            stage: _stats(values) for stage, values in warm_stage_values.items()
        },
        "raw_artifact_sha256": {
            f"run-{index:02d}/result.json": _sha256_file(output / f"run-{index:02d}" / "result.json")
            for index in range(1, RUN_COUNT + 1)
        } | {
            f"run-{index:02d}/retrieval.jsonl": _sha256_file(output / f"run-{index:02d}" / "retrieval.jsonl")
            for index in range(1, RUN_COUNT + 1)
        },
    }
    _write_json_exclusive(output / "summary-v2.json", corrected)

    metrics = corrected["metrics_across_runs"]
    warm = corrected["warm_stage_ms_all_357_queries"]
    cold = corrected["first_query_stage_ms_across_runs"]
    mib = 1024 * 1024
    lines = [
        "# P1 优化前基线（修正版）", "",
        f"- 状态：{corrected['status']}",
        "- 原始结果未修改；本报告修正可回答题计数，并补充三轮分阶段聚合。",
        f"- 范围：{EXPECTED_DOCUMENTS} 篇文档 / {EXPECTED_CHUNKS} chunks / "
        f"{EXPECTED_QUESTIONS} 题 / {RUN_COUNT} 个独立冷进程", "",
        "## 质量", "",
    ]
    lines.extend(f"- {key}: {value}" for key, value in quality.items())
    lines.extend(["", "## 系统时间（跨3轮）", ""])
    for key in ("cold_first_query_s", "warm_mean_query_s", "warm_p95_query_s",
                "wall_120_queries_s", "cpu_time_total_s"):
        value = metrics[key]
        lines.append(
            f"- {key}: mean={value['mean']:.6f}, min={min(value_key for value_key in [runs[i]['retrieval']['cold_first_query_s'] if key == 'cold_first_query_s' else runs[i]['retrieval']['warm_queries_s']['mean'] if key == 'warm_mean_query_s' else runs[i]['retrieval']['warm_queries_s']['p95'] if key == 'warm_p95_query_s' else runs[i]['retrieval']['wall_120_queries_s'] if key == 'wall_120_queries_s' else runs[i]['resources']['cpu_time_s']['total'] for i in range(RUN_COUNT)]):.6f}, max={value['max']:.6f} s"
        )
    lines.extend(["", "## 分阶段耗时", ""])
    for stage in stage_names:
        lines.append(
            f"- {stage}: 首问均值={cold[stage]['mean']:.3f} ms；"
            f"暖态均值={warm[stage]['mean']:.3f} ms；暖态P95={warm[stage]['p95']:.3f} ms"
        )
    lines.extend(["", "## 资源（3轮均值）", ""])
    for key in ("peak_rss_bytes", "peak_private_bytes", "gpu_peak_allocated_bytes",
                "gpu_peak_reserved_bytes", "disk_read_bytes", "disk_write_bytes"):
        lines.append(f"- {key}: {metrics[key]['mean'] / mib:.2f} MiB")
    lines.extend(["", "## 完整性检查", ""])
    lines.extend(f"- {key}: {value}" for key, value in corrected["checks"].items())
    lines.append("")
    _write_text_exclusive(output / "report-v2.md", "\n".join(lines))
    print(json.dumps({"status": corrected["status"], "summary": str(output / "summary-v2.json")}, ensure_ascii=False))


def _parent(output: Path) -> None:
    if output.exists():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖：{output}")
    if not SOURCE_STORAGE.is_dir():
        raise FileNotFoundError(f"封存评测存储不存在：{SOURCE_STORAGE}")
    output.mkdir(parents=True)
    source_before = _tree_fingerprint(SOURCE_STORAGE)
    copy_times: list[float] = []
    for index in range(1, RUN_COUNT + 1):
        run_dir = output / f"run-{index:02d}"
        storage = run_dir / "work" / "storage"
        started = time.perf_counter()
        shutil.copytree(SOURCE_STORAGE, storage)
        copy_times.append(time.perf_counter() - started)
        print(f"启动冷进程 {index}/{RUN_COUNT}", flush=True)
        subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--worker", "--run-dir", str(run_dir)],
            cwd=PROJECT_ROOT,
            check=True,
        )
    source_after = _tree_fingerprint(SOURCE_STORAGE)
    runs = [json.loads((output / f"run-{index:02d}" / "result.json").read_text(encoding="utf-8"))
            for index in range(1, RUN_COUNT + 1)]
    summary = _aggregate(output, runs, source_before, source_after, copy_times)
    _write_json_exclusive(output / "summary.json", summary)
    _write_text_exclusive(output / "report.md", _report(summary))
    print(json.dumps({"status": summary["status"], "output": str(output)}, ensure_ascii=False), flush=True)
    if summary["status"] != "passed":
        raise RuntimeError(f"P1基线完整性检查失败：{summary['checks']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=HERE / "runs" / "old-baseline-20260903")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--reaggregate", type=Path)
    args = parser.parse_args()
    if args.reaggregate is not None:
        _reaggregate_v2(args.reaggregate.resolve())
    elif args.worker:
        if args.run_dir is None:
            parser.error("--worker 需要 --run-dir")
        _worker(args.run_dir.resolve())
    else:
        _parent(args.output.resolve())


if __name__ == "__main__":
    main()
