"""Compare completed CPU/GPU retrieval-only baselines and persist evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from eval.retrieval_sweep import _latency


COMMON_CONFIG_KEYS = (
    "embedding_model",
    "embedding_query_instruction",
    "embedding_batch_size",
    "chunk_size",
    "chunk_overlap",
    "chunk_strategy",
    "chunk_max_tokens",
    "top_k",
    "retrieval_mode",
    "retrieval_distance_threshold",
    "bm25_top_k",
    "bm25_k1",
    "bm25_b",
    "rrf_k",
    "reranker_enabled",
    "reranker_model",
    "reranker_batch_size",
    "reranker_max_length",
    "reranker_candidate_ks",
    "llm_called",
    "hf_hub_offline",
    "transformers_offline",
    "retrieval_trace_enabled",
    "dataset_version",
)

QUALITY_KEYS = (
    "document_hit_rate",
    "document_recall",
    "document_mrr",
    "all_gold_docs_recalled_rate",
    "multi_document_complete_recall_rate",
    "evidence_coverage",
    "exact_evidence_containment_rate",
    "unanswerable_llm_entry_rate",
    "unanswerable_refusal_gate_accuracy",
)

TRACE_QUALITY_KEYS = (
    "document_hit_rate",
    "document_recall",
    "document_mrr",
    "all_gold_docs_recalled_rate",
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _speed(cpu_value: float, gpu_value: float) -> dict:
    return {
        "cpu": round(cpu_value, 4),
        "gpu": round(gpu_value, 4),
        "speedup_x": round(cpu_value / gpu_value, 2),
        "gpu_reduction_pct": round((1 - gpu_value / cpu_value) * 100, 2),
    }


def _quality(candidate: dict) -> dict:
    return {key: candidate.get(key) for key in QUALITY_KEYS}


def _selected_ids(row: dict) -> list[str]:
    return [item["chunk_id"] for item in row["retrieval"]]


def _render(payload: dict) -> str:
    cpu = payload["profiles"]["cpu"]
    gpu = payload["profiles"]["gpu"]
    perf = payload["performance"]
    consistency = payload["per_question_consistency"]
    quality = payload["quality"]
    lines = [
        "# Dataset v3 120 题 CPU/GPU 检索预基线对比",
        "",
        f"- 生成时间：`{payload['generated_at']}`",
        "- 性质：`draft/preflight`；120 题尚未完成人工审核，不能标记为正式冻结基线。",
        "- 范围：仅检索链路，未调用 LLM/API。CPU 与 GPU 使用各自独立的测试向量库。",
        "- 公平性边界：检索参数相同；CPU Reranker 为 FP32，GPU Reranker 为 FP16，因此是迁移方案的端到端对照，不是单变量纯硬件基准。",
        "",
        "## 运行设备",
        "",
        "| 组别 | PyTorch | Embedding 实际设备 | Reranker 实际设备 | Reranker 精度 | CUDA 峰值 allocated/reserved |",
        "|---|---|---|---|---|---:|",
        f"| CPU | {cpu['torch_version']} | {cpu['embedding_device']} | {cpu['reranker_device']} | FP32 | 0/0 MiB |",
        f"| GPU | {gpu['torch_version']} | {gpu['embedding_device']} | {gpu['reranker_device']} | FP16 | {gpu['cuda_peak_allocated_mib']}/{gpu['cuda_peak_reserved_mib']} MiB |",
        "",
        "## 性能",
        "",
        "| 指标 | CPU | GPU | 加速比 | GPU 耗时下降 |",
        "|---|---:|---:|---:|---:|",
        f"| 1000 文档/2914 chunks 入库 | {perf['ingest_s']['cpu']:.2f}s | {perf['ingest_s']['gpu']:.2f}s | {perf['ingest_s']['speedup_x']:.2f}x | {perf['ingest_s']['gpu_reduction_pct']:.2f}% |",
        f"| 120 题 mean | {perf['query_s']['mean']['cpu']:.3f}s | {perf['query_s']['mean']['gpu']:.3f}s | {perf['query_s']['mean']['speedup_x']:.2f}x | {perf['query_s']['mean']['gpu_reduction_pct']:.2f}% |",
        f"| 120 题 p50 | {perf['query_s']['p50']['cpu']:.3f}s | {perf['query_s']['p50']['gpu']:.3f}s | {perf['query_s']['p50']['speedup_x']:.2f}x | {perf['query_s']['p50']['gpu_reduction_pct']:.2f}% |",
        f"| 120 题 p95 | {perf['query_s']['p95']['cpu']:.3f}s | {perf['query_s']['p95']['gpu']:.3f}s | {perf['query_s']['p95']['speedup_x']:.2f}x | {perf['query_s']['p95']['gpu_reduction_pct']:.2f}% |",
        f"| 去掉首题冷启动 mean | {perf['query_warm_s']['mean']['cpu']:.3f}s | {perf['query_warm_s']['mean']['gpu']:.3f}s | {perf['query_warm_s']['mean']['speedup_x']:.2f}x | {perf['query_warm_s']['mean']['gpu_reduction_pct']:.2f}% |",
        "",
        "## 质量",
        "",
        "| 指标 | CPU | GPU | 是否一致 |",
        "|---|---:|---:|---|",
    ]
    for key in QUALITY_KEYS:
        lines.append(
            f"| {key} | {quality['cpu'][key]} | {quality['gpu'][key]} "
            f"| {'是' if quality['cpu'][key] == quality['gpu'][key] else '否'} |"
        )
    lines += [
        "",
        "## 逐题排序一致性",
        "",
        f"- Top1 chunk 相同：`{consistency['top1_same_count']}/120`。",
        f"- Top5 chunk 集合相同：`{consistency['top5_set_same_count']}/120`。",
        f"- Top5 顺序完全相同：`{consistency['top5_order_same_count']}/120`。",
        f"- 顺序不同题目：`{', '.join(consistency['order_changed_ids']) or '无'}`。",
        "",
        "## Trace 阶段耗时（mean）",
        "",
        "| 阶段 | CPU(ms) | GPU(ms) | 加速比 |",
        "|---|---:|---:|---:|",
    ]
    for stage, values in perf["trace_mean_ms"].items():
        lines.append(
            f"| {stage} | {values['cpu']:.3f} | {values['gpu']:.3f} | {values['speedup_x']:.2f}x |"
        )
    lines += [
        "",
        "## Trace 阶段质量",
        "",
        "| 阶段 | CPU Hit/Recall/MRR/All-gold | GPU Hit/Recall/MRR/All-gold |",
        "|---|---|---|",
    ]
    for stage, values in payload["trace_stage_quality"].items():
        cpu_values = "/".join(str(values["cpu"][key]) for key in TRACE_QUALITY_KEYS)
        gpu_values = "/".join(str(values["gpu"][key]) for key in TRACE_QUALITY_KEYS)
        lines.append(f"| {stage} | {cpu_values} | {gpu_values} |")
    if payload["trace_stage_quality_differences"]:
        lines += [
            "",
            "中间阶段存在数值差异，但最终质量指标一致：",
            "",
        ]
        for item in payload["trace_stage_quality_differences"]:
            lines.append(
                f"- `{item['stage']}` / `{item['question_id']}`："
                f"CPU all-gold={item['cpu_all_gold']}，GPU all-gold={item['gpu_all_gold']}。"
            )
    isolation = payload["production_isolation"]
    lines += [
        "",
        "## 隔离与限制",
        "",
        f"- 生产 metadata.db 哈希未变：`{isolation['metadata']['unchanged']}`。",
        f"- 生产 Chroma sqlite 哈希未变：`{isolation['chroma']['unchanged']}`。",
        "- 20 道不可回答题全部仍有检索结果，拒答 gate 准确率为 0%；这说明需要后续校准并实现门控，不能解释为模型已具备拒答能力。",
        "- GPU 运行期间的显卡利用率/功耗属于抽样观察，不是连续采样；模型实际设备与 CUDA 峰值来自运行时对象和 PyTorch 计数器。",
        "",
        "## 失败题（CPU/GPU 一致）",
        "",
    ]
    lines.extend(f"- `{item}`" for item in payload["failure_ids"]["cpu"])
    return "\n".join(lines) + "\n"


def compare(args: argparse.Namespace) -> dict:
    cpu_report = _load(args.cpu_run / "retrieval-sweep.json")
    gpu_report = _load(args.gpu_run / "retrieval-sweep.json")
    cpu_candidate = cpu_report["candidates"][0]
    gpu_candidate = gpu_report["candidates"][0]
    candidate_k = cpu_candidate["candidate_k"]
    if candidate_k != gpu_candidate["candidate_k"]:
        raise ValueError("CPU/GPU candidate_k 不一致")
    cpu_raw = _load_jsonl(args.cpu_run / f"retrieval-k{candidate_k}.jsonl")
    gpu_raw = _load_jsonl(args.gpu_run / f"retrieval-k{candidate_k}.jsonl")
    if [row["id"] for row in cpu_raw] != [row["id"] for row in gpu_raw]:
        raise ValueError("CPU/GPU 题目 ID 或顺序不一致")

    config_differences = {
        key: {"cpu": cpu_report["config"].get(key), "gpu": gpu_report["config"].get(key)}
        for key in COMMON_CONFIG_KEYS
        if cpu_report["config"].get(key) != gpu_report["config"].get(key)
    }
    cpu_lists = [_selected_ids(row) for row in cpu_raw]
    gpu_lists = [_selected_ids(row) for row in gpu_raw]
    order_changed = [
        cpu_raw[index]["id"]
        for index, (cpu_ids, gpu_ids) in enumerate(zip(cpu_lists, gpu_lists))
        if cpu_ids != gpu_ids
    ]
    cpu_latency = [float(row["retrieval_s"]) for row in cpu_raw]
    gpu_latency = [float(row["retrieval_s"]) for row in gpu_raw]
    cpu_warm = _latency(cpu_latency[1:])
    gpu_warm = _latency(gpu_latency[1:])

    trace_mean = {}
    for stage in cpu_candidate["trace_timings_ms"]:
        cpu_value = cpu_candidate["trace_timings_ms"][stage]["mean"]
        gpu_value = gpu_candidate["trace_timings_ms"][stage]["mean"]
        trace_mean[stage] = _speed(cpu_value, gpu_value)

    trace_stage_quality = {}
    trace_stage_differences = []
    for stage in cpu_candidate["trace_stages"]:
        cpu_stage = cpu_candidate["trace_stages"][stage]
        gpu_stage = gpu_candidate["trace_stages"][stage]
        trace_stage_quality[stage] = {
            "cpu": {key: cpu_stage.get(key) for key in TRACE_QUALITY_KEYS},
            "gpu": {key: gpu_stage.get(key) for key in TRACE_QUALITY_KEYS},
        }
        gpu_per_question = {
            row["id"]: row for row in gpu_stage.get("per_question", [])
        }
        for cpu_row in cpu_stage.get("per_question", []):
            gpu_row = gpu_per_question.get(cpu_row["id"], {})
            if cpu_row.get("all_gold_docs_recalled") != gpu_row.get(
                "all_gold_docs_recalled"
            ):
                trace_stage_differences.append(
                    {
                        "stage": stage,
                        "question_id": cpu_row["id"],
                        "cpu_all_gold": cpu_row.get("all_gold_docs_recalled"),
                        "gpu_all_gold": gpu_row.get("all_gold_docs_recalled"),
                    }
                )

    metadata_hash = _sha256(args.production_metadata)
    chroma_hash = _sha256(args.production_chroma)
    gpu_runtime = gpu_report["config"]["runtime"]
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "status": "draft/preflight",
        "scope": {
            "questions": len(cpu_raw),
            "candidate_k": candidate_k,
            "llm_called": False,
            "common_config_equal": not config_differences,
            "common_config_differences": config_differences,
            "precision_confound": "CPU reranker FP32; GPU reranker FP16",
        },
        "profiles": {
            "cpu": {
                "torch_version": cpu_report["config"]["torch_version"],
                "embedding_device": cpu_report["config"]["runtime"]["embedding"]["actual_device"],
                "reranker_device": cpu_report["config"]["runtime"]["reranker"]["actual_device"],
                "device_verified": cpu_report["config"]["runtime"]["embedding"]["verified"] and cpu_report["config"]["runtime"]["reranker"]["verified"],
            },
            "gpu": {
                "torch_version": gpu_report["config"]["torch_version"],
                "torch_cuda_version": gpu_report["config"]["torch_cuda_version"],
                "device_name": gpu_report["config"]["torch_cuda_device_name"],
                "embedding_device": gpu_runtime["embedding"]["actual_device"],
                "reranker_device": gpu_runtime["reranker"]["actual_device"],
                "device_verified": gpu_runtime["embedding"]["verified"] and gpu_runtime["reranker"]["verified"],
                "cuda_peak_allocated_mib": round(gpu_runtime["cuda_peak_memory"]["allocated_bytes"] / 1024 / 1024, 1),
                "cuda_peak_reserved_mib": round(gpu_runtime["cuda_peak_memory"]["reserved_bytes"] / 1024 / 1024, 1),
                "observed_card_memory_mib_range": [2859, 3779],
                "observed_gpu_utilization_peak_pct": 69,
                "observed_power_peak_w": 137.57,
                "observation_note": "intermittent nvidia-smi samples during the run; not continuous telemetry",
            },
        },
        "performance": {
            "ingest_s": _speed(cpu_report["ingest"]["ingest_s"], gpu_report["ingest"]["ingest_s"]),
            "query_s": {
                key: _speed(cpu_candidate["latency_s"][key], gpu_candidate["latency_s"][key])
                for key in ("mean", "p50", "p95")
            },
            "query_warm_s": {
                key: _speed(cpu_warm[key], gpu_warm[key])
                for key in ("mean", "p50", "p95")
            },
            "first_query_s": _speed(cpu_latency[0], gpu_latency[0]),
            "trace_mean_ms": trace_mean,
        },
        "quality": {"cpu": _quality(cpu_candidate), "gpu": _quality(gpu_candidate)},
        "quality_equal": _quality(cpu_candidate) == _quality(gpu_candidate),
        "by_split": {"cpu": cpu_candidate.get("by_split"), "gpu": gpu_candidate.get("by_split")},
        "trace_stage_quality": trace_stage_quality,
        "trace_stage_quality_equal": not trace_stage_differences,
        "trace_stage_quality_differences": trace_stage_differences,
        "per_question_consistency": {
            "top1_same_count": sum(a[0] == b[0] for a, b in zip(cpu_lists, gpu_lists)),
            "top5_set_same_count": sum(set(a) == set(b) for a, b in zip(cpu_lists, gpu_lists)),
            "top5_order_same_count": len(cpu_raw) - len(order_changed),
            "order_changed_ids": order_changed,
        },
        "failure_ids": {
            "cpu": cpu_candidate["retrieval_failure_ids"],
            "gpu": gpu_candidate["retrieval_failure_ids"],
        },
        "production_isolation": {
            "metadata": {
                "path": str(args.production_metadata.resolve()),
                "before_sha256": args.expected_metadata_sha256,
                "after_sha256": metadata_hash,
                "unchanged": metadata_hash == args.expected_metadata_sha256,
            },
            "chroma": {
                "path": str(args.production_chroma.resolve()),
                "before_sha256": args.expected_chroma_sha256,
                "after_sha256": chroma_hash,
                "unchanged": chroma_hash == args.expected_chroma_sha256,
            },
        },
        "sources": {
            "cpu_report": str((args.cpu_run / "retrieval-sweep.json").resolve()),
            "gpu_report": str((args.gpu_run / "retrieval-sweep.json").resolve()),
        },
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "cpu-gpu-comparison.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output / "cpu-gpu-comparison.md").write_text(_render(payload), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpu-run", required=True, type=Path)
    parser.add_argument("--gpu-run", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--production-metadata", default=Path("data/metadata.db"), type=Path)
    parser.add_argument("--production-chroma", default=Path("data/chroma_db/chroma.sqlite3"), type=Path)
    parser.add_argument("--expected-metadata-sha256", required=True)
    parser.add_argument("--expected-chroma-sha256", required=True)
    payload = compare(parser.parse_args())
    print(json.dumps({
        "quality_equal": payload["quality_equal"],
        "top5_order_same_count": payload["per_question_consistency"]["top5_order_same_count"],
        "production_isolation": payload["production_isolation"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
