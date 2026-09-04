"""V3-5 B：在同一GPU进程内运行六组检索模式消融。

本入口复用A阶段复制的隔离索引，只执行development 72题，不调用生成模型，
不修改生产默认值。Hybrid+Reranker control在首尾各跑一次，用于确认同一进程
内的ANN状态没有漂移；六个唯一配置各只计一次质量结果。
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from eval.crud_rag_1000.v3_1 import tuning_runner as runner


CONFIG_SPECS = (
    ("hybrid_reranker", "hybrid", True),
    ("dense_reranker", "dense", True),
    ("bm25_reranker", "bm25", True),
    ("hybrid_no_reranker", "hybrid", False),
    ("dense_no_reranker", "dense", False),
    ("bm25_no_reranker", "bm25", False),
)

PASS_SCHEDULE = (
    ("01-control-pre", "hybrid_reranker"),
    ("02-dense-reranker", "dense_reranker"),
    ("03-bm25-reranker", "bm25_reranker"),
    ("04-hybrid-no-reranker", "hybrid_no_reranker"),
    ("05-dense-no-reranker", "dense_no_reranker"),
    ("06-bm25-no-reranker", "bm25_no_reranker"),
    ("07-control-post", "hybrid_reranker"),
)

QUALITY_METRICS = (
    "document_hit_rate",
    "document_recall",
    "document_mrr",
    "all_gold_docs_recalled_rate",
    "multi_document_complete_recall_rate",
    "evidence_coverage",
    "exact_evidence_containment_rate",
    "unanswerable_llm_entry_rate",
)

STAGE_METRICS = (
    "document_hit_rate",
    "document_recall",
    "document_mrr",
    "all_gold_docs_recalled_rate",
)


def _phase_b_configs() -> dict[str, dict[str, Any]]:
    configs: dict[str, dict[str, Any]] = {}
    for name, mode, reranker_enabled in CONFIG_SPECS:
        payload = deepcopy(runner.BASELINE_CONFIG)
        payload["mode"] = mode
        payload["reranker_enabled"] = reranker_enabled
        configs[name] = runner._validated_config_payload(payload)
    if len({runner._config_id(value) for value in configs.values()}) != 6:
        raise RuntimeError("B阶段六个配置的config_id不唯一")
    return configs


def _compact_quality(candidate: dict[str, Any]) -> dict[str, Any]:
    return {key: candidate.get(key) for key in QUALITY_METRICS}


def _stage_quality(candidate: dict[str, Any]) -> dict[str, dict[str, Any]]:
    stages = candidate.get("trace_metrics", {}).get("stages", {})
    return {
        stage: {key: payload.get(key) for key in STAGE_METRICS}
        for stage, payload in stages.items()
    }


def _orders(rows: list[dict[str, Any]], stage: str = "selected") -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for row in rows:
        if stage == "selected":
            candidates = row["retrieval"]
        else:
            trace_stages = {
                item["name"]: item["candidates"]
                for item in row["trace"]["stages"]
            }
            candidates = trace_stages.get(stage, [])
        result[row["id"]] = [item["chunk_id"] for item in candidates]
    return result


def _control_stability(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
) -> dict[str, Any]:
    if [row["id"] for row in before] != [row["id"] for row in after]:
        raise RuntimeError("control前后题目ID或顺序不一致")
    stages = ("dense", "dense_raw", "dense_eligible", "bm25", "rrf", "reranker", "selected")
    stage_matches: dict[str, int] = {}
    stage_differences: dict[str, list[str]] = {}
    for stage in stages:
        left = _orders(before, stage)
        right = _orders(after, stage)
        differences = [qid for qid in left if left[qid] != right[qid]]
        stage_matches[stage] = len(left) - len(differences)
        stage_differences[stage] = differences
    return {
        "question_count": len(before),
        "stage_order_identical_count": stage_matches,
        "stage_order_difference_ids": stage_differences,
        "passed": all(not value for value in stage_differences.values()),
    }


def _question_priority(row: dict[str, Any]) -> tuple[float, ...]:
    return (
        float(bool(row.get("all_gold_docs_recalled"))),
        float(row.get("document_recall_at_k") or 0.0),
        float(bool(row.get("document_hit_at_k"))),
        float(row.get("evidence_coverage") or 0.0),
        float(row.get("document_mrr") or 0.0),
    )


def _compare_to_control(
    control_candidate: dict[str, Any],
    candidate: dict[str, Any],
    control_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    control_per_question = {
        row["id"]: row for row in control_candidate["per_question"]
        if row["answerable"]
    }
    candidate_per_question = {
        row["id"]: row for row in candidate["per_question"]
        if row["answerable"]
    }
    improved: list[str] = []
    regressed: list[str] = []
    mixed: list[str] = []
    for qid, baseline in control_per_question.items():
        left = _question_priority(baseline)
        right = _question_priority(candidate_per_question[qid])
        gains = any(new > old for old, new in zip(left, right))
        losses = any(new < old for old, new in zip(left, right))
        if gains and losses:
            mixed.append(qid)
        elif gains:
            improved.append(qid)
        elif losses:
            regressed.append(qid)

    control_selected = _orders(control_rows)
    candidate_selected = _orders(candidate_rows)
    order_differences = [
        qid for qid in control_selected
        if control_selected[qid] != candidate_selected[qid]
    ]
    set_differences = [
        qid for qid in control_selected
        if set(control_selected[qid]) != set(candidate_selected[qid])
    ]
    deltas = {}
    for metric in QUALITY_METRICS:
        before = control_candidate.get(metric)
        after = candidate.get(metric)
        deltas[metric] = None if before is None or after is None else round(after - before, 6)
    return {
        "quality_delta": deltas,
        "answerable_improved_ids": improved,
        "answerable_regressed_ids": regressed,
        "answerable_mixed_ids": mixed,
        "selected_order_difference_count": len(order_differences),
        "selected_order_difference_ids": order_differences,
        "selected_set_difference_count": len(set_differences),
        "selected_set_difference_ids": set_differences,
    }


def _render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# V3-5 B 模式消融结果",
        "",
        f"- Run：`{payload['run_id']}`",
        "- 范围：development 72题（60可回答+12不可回答）",
        "- 固定项：候选K15、Dense阈值0.67、RRF60、Top5、每文档最多3块",
        "- 执行：同一GPU进程；首尾重复Hybrid+Reranker control；无生成模型调用",
        "",
        "## 汇总",
        "",
        "| 配置 | Hit | Recall | MRR | all-gold | 多文档完整 | Evidence | 不可回答非空 | 暖态mean(s) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["configurations"]:
        quality = row["quality"]
        latency = row["latency"]
        values = [
            row["name"],
            quality["document_hit_rate"],
            quality["document_recall"],
            quality["document_mrr"],
            quality["all_gold_docs_recalled_rate"],
            quality["multi_document_complete_recall_rate"],
            quality["evidence_coverage"],
            quality["unanswerable_llm_entry_rate"],
            latency["mean"],
        ]
        lines.append(
            f"| {values[0]} | " + " | ".join(
                "—" if value is None else f"{value:.4f}"
                for value in values[1:]
            ) + " |"
        )
    lines += [
        "",
        "## 相对control的逐题变化",
        "",
    ]
    for name, comparison in payload["comparisons_to_control"].items():
        improved = "、".join(comparison["answerable_improved_ids"]) or "无"
        regressed = "、".join(comparison["answerable_regressed_ids"]) or "无"
        mixed = "、".join(comparison["answerable_mixed_ids"]) or "无"
        lines += [
            f"### {name}",
            "",
            f"- 改善：{improved}",
            f"- 回归：{regressed}",
            f"- 混合变化：{mixed}",
            f"- 最终Top5顺序/集合变化题数：{comparison['selected_order_difference_count']} / {comparison['selected_set_difference_count']}",
            "",
        ]
    stability = payload["control_stability"]
    lines += [
        "## 控制与边界",
        "",
        f"- control首尾逐阶段顺序稳定：{'通过' if stability['passed'] else '失败'}",
        f"- Embedding/Reranker实际设备：{payload['runtime']['runtime']['embedding']['actual_device']} / {payload['runtime']['runtime']['reranker']['actual_device']}",
        f"- LLM模块导入：{payload['llm_module_imported']}；出站连接：{len(payload['outbound_ip_connections'])}",
        "- 本轮只完成B模式消融，不制定生产默认值，不执行C阶段候选深度和阈值扫描。",
        "- 单次顺序运行耗时仅作本轮资源记录，不作为F阶段稳定性能结论。",
        "",
    ]
    return "\n".join(lines)


def run_phase_b(experiment_id: str, run_id: str) -> dict[str, Any]:
    experiment, protocol = runner._load_protocol(experiment_id)
    storage_before = runner._validate_experiment_storage(experiment, protocol)
    acceptance_path = experiment / "phase-a-acceptance.json"
    dense_analysis_path = experiment / "diagnostics" / "dense-variance-analysis.json"
    if not acceptance_path.is_file() or runner._load_json(acceptance_path)["status"] != "passed":
        raise RuntimeError("A阶段未通过，不能进入B")
    if not dense_analysis_path.is_file():
        raise RuntimeError("Dense差异诊断缺失，不能进入B")
    dense_analysis = runner._load_json(dense_analysis_path)
    if dense_analysis.get("status") != "confirmed_with_observed_range":
        raise RuntimeError("Dense差异诊断状态不符合B前置条件")

    run_id = runner._safe_name(run_id, "run_id")
    phase_root = runner._assert_within(experiment / "phase-b" / run_id, experiment)
    phase_root.mkdir(parents=True, exist_ok=False)
    (phase_root / "configs").mkdir()
    (phase_root / "passes").mkdir()

    dataset = runner._dataset()
    if runner._sha256_file(runner.DATASET_PATH) != protocol["dataset"]["sha256"]:
        raise RuntimeError("题库hash与A协议不一致")
    questions = runner._split_questions(dataset, "development")
    if [row["id"] for row in questions] != protocol["splits"]["development"]:
        raise RuntimeError("development题目ID与A协议不一致")

    configs = _phase_b_configs()
    config_records = {}
    for name, config in configs.items():
        record = {
            "name": name,
            "config_id": runner._config_id(config),
            "retrieval": config,
            "effective_config": runner._effective_config(config),
            "derived": {"fusion_pool_k": runner._fusion_pool_k(config)},
        }
        config_records[name] = record
        runner._write_json_exclusive(phase_root / "configs" / f"{name}.json", record)

    phase_protocol = {
        "schema_version": 1,
        "phase": "V3-5-B",
        "status": "running",
        "run_id": run_id,
        "created_at": runner._now(),
        "parent_experiment_id": experiment_id,
        "parent_protocol_identity": protocol["protocol_identity"],
        "dataset_sha256": protocol["dataset"]["sha256"],
        "storage_identity": protocol["source"]["storage_identity"],
        "question_count": len(questions),
        "unique_configuration_count": len(configs),
        "pass_schedule": [
            {"pass": pass_name, "configuration": config_name}
            for pass_name, config_name in PASS_SCHEDULE
        ],
        "control_strategy": "single_process_control_pre_and_post",
        "fixed_parameters": {
            key: runner.BASELINE_CONFIG[key]
            for key in runner.BASELINE_CONFIG
            if key not in ("mode", "reranker_enabled")
        },
        "runner_sha256": runner._sha256_file(Path(__file__).resolve()),
        "retrieval_code_identity": runner._retrieval_code_identity(),
        "environment": protocol["environment"],
        "network_policy": protocol["network_policy"],
        "llm_policy": protocol["llm_policy"],
        "limits": [
            "development only",
            "no production settings change",
            "no generation model",
            "no C-stage parameter sweep",
            "latency is descriptive only",
        ],
    }
    runner._write_json_exclusive(phase_root / "protocol.json", phase_protocol)

    storage = experiment / "storage"
    os.environ.update({
        **protocol["environment"],
        "CHROMA_PERSIST_DIR": str(storage / "chroma"),
        "METADATA_DB_PATH": str(storage / "metadata.db"),
        "UPLOAD_DIR": str(storage / "uploads"),
    })
    blocked: list[dict[str, Any]] = []
    started = time.perf_counter()
    runner._install_network_guard(blocked, started)
    from eval import adapters
    from eval.retrieval_sweep import build_candidate_result

    adapters.assert_isolation()
    control_config = configs["hybrid_reranker"]
    warmup = adapters.retrieve_only(
        questions[0]["question"],
        include_trace=True,
        runtime_config=runner._runtime_config(control_config),
    )
    if warmup["trace"]["effective_config"] != runner._effective_config(control_config):
        raise RuntimeError("B预热control参数未实际生效")
    runner._write_json_exclusive(
        phase_root / "warmup.json",
        {
            "id": questions[0]["id"],
            "retrieval_s": warmup["retrieval_s"],
            "effective_config": warmup["trace"]["effective_config"],
            "not_in_quality_metrics": True,
        },
    )

    pass_rows: dict[str, list[dict[str, Any]]] = {}
    pass_candidates: dict[str, dict[str, Any]] = {}
    pass_latencies: dict[str, dict[str, Any]] = {}
    for pass_index, (pass_name, config_name) in enumerate(PASS_SCHEDULE, 1):
        config = configs[config_name]
        runtime_config = runner._runtime_config(config)
        rows_path = phase_root / "passes" / f"{pass_name}.jsonl"
        rows: list[dict[str, Any]] = []
        print(f"[B {pass_index}/{len(PASS_SCHEDULE)}] {pass_name}", flush=True)
        with rows_path.open("x", encoding="utf-8") as handle:
            for index, question in enumerate(questions, 1):
                result = adapters.retrieve_only(
                    question["question"],
                    include_trace=True,
                    runtime_config=runtime_config,
                )
                trace = result.get("trace")
                if trace is None or trace.get("effective_config") != runner._effective_config(config):
                    raise RuntimeError(f"{pass_name}参数未生效或Trace缺失：{question['id']}")
                row = {
                    "id": question["id"],
                    "pass": pass_name,
                    "configuration": config_name,
                    "config_id": config_records[config_name]["config_id"],
                    "retrieval": result["retrieval"],
                    "retrieval_s": result["retrieval_s"],
                    "trace": trace,
                }
                rows.append(row)
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                if index % 12 == 0:
                    print(f"  {index}/{len(questions)}", flush=True)
        candidate = build_candidate_result(
            candidate_k=config["reranker_candidate_k"],
            questions=questions,
            retrievals={row["id"]: row["retrieval"] for row in rows},
            timings_s=[row["retrieval_s"] for row in rows],
            traces={row["id"]: row["trace"] for row in rows},
        )
        pass_rows[pass_name] = rows
        pass_candidates[pass_name] = candidate
        pass_latencies[pass_name] = runner._latency(rows)
        runner._write_json_exclusive(
            phase_root / "passes" / f"{pass_name}-summary.json",
            {
                "pass": pass_name,
                "configuration": config_name,
                "config_id": config_records[config_name]["config_id"],
                "question_count": len(rows),
                "quality": _compact_quality(candidate),
                "stage_quality": _stage_quality(candidate),
                "latency": pass_latencies[pass_name],
                "candidate_result": candidate,
            },
        )

    runtime = adapters.get_run_config()
    for component in ("embedding", "reranker"):
        status = runtime["runtime"][component]
        if not (
            status["loaded"]
            and status["verified"]
            and status["actual_device"] == "cuda:0"
        ):
            raise RuntimeError(f"B阶段{component}未在cuda:0真实运行")
    if "backend.services.llm" in sys.modules:
        raise RuntimeError("B阶段意外导入LLM模块")
    if blocked:
        raise RuntimeError("B阶段出现出站网络尝试")

    storage_after = runner._validate_experiment_storage(experiment, protocol)
    control_pre_name = "01-control-pre"
    control_post_name = "07-control-post"
    control_stability = _control_stability(
        pass_rows[control_pre_name],
        pass_rows[control_post_name],
    )
    if _compact_quality(pass_candidates[control_pre_name]) != _compact_quality(pass_candidates[control_post_name]):
        control_stability["quality_exact_match"] = False
        control_stability["passed"] = False
    else:
        control_stability["quality_exact_match"] = True
    if not control_stability["passed"]:
        raise RuntimeError("B阶段control首尾不稳定，拒绝解释消融结果")

    primary_pass = {
        "hybrid_reranker": control_pre_name,
        "dense_reranker": "02-dense-reranker",
        "bm25_reranker": "03-bm25-reranker",
        "hybrid_no_reranker": "04-hybrid-no-reranker",
        "dense_no_reranker": "05-dense-no-reranker",
        "bm25_no_reranker": "06-bm25-no-reranker",
    }
    control_candidate = pass_candidates[control_pre_name]
    control_rows = pass_rows[control_pre_name]
    configurations = []
    comparisons = {}
    for name, pass_name in primary_pass.items():
        candidate = pass_candidates[pass_name]
        configurations.append({
            "name": name,
            "pass": pass_name,
            "config_id": config_records[name]["config_id"],
            "effective_config": config_records[name]["effective_config"],
            "derived": config_records[name]["derived"],
            "quality": _compact_quality(candidate),
            "stage_quality": _stage_quality(candidate),
            "latency": pass_latencies[pass_name],
        })
        if name != "hybrid_reranker":
            comparisons[name] = _compare_to_control(
                control_candidate,
                candidate,
                control_rows,
                pass_rows[pass_name],
            )

    summary = {
        "schema_version": 1,
        "phase": "V3-5-B",
        "status": "passed",
        "run_id": run_id,
        "parent_experiment_id": experiment_id,
        "completed_at": runner._now(),
        "wall_s": round(time.perf_counter() - started, 3),
        "question_count": len(questions),
        "unique_configuration_count": len(configs),
        "real_retrieval_query_count": len(questions) * len(PASS_SCHEDULE) + 1,
        "quality_query_count": len(questions) * len(PASS_SCHEDULE),
        "warmup_query_count": 1,
        "configurations": configurations,
        "comparisons_to_control": comparisons,
        "control_stability": control_stability,
        "runtime": runtime,
        "llm_module_imported": False,
        "outbound_ip_connections": blocked,
        "storage_before": storage_before,
        "storage_after": storage_after,
        "limits": phase_protocol["limits"],
    }
    runner._write_json_exclusive(phase_root / "phase-b-summary.json", summary)
    (phase_root / "phase-b-report.md").write_text(
        _render_report(summary), encoding="utf-8"
    )
    completed_protocol = {**phase_protocol, "status": "passed", "completed_at": summary["completed_at"]}
    runner._write_json_exclusive(phase_root / "protocol-completed.json", completed_protocol)
    return {"phase_root": str(phase_root), "summary": summary}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-id", default="v35-a-baseline-20260829")
    parser.add_argument("--run-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_phase_b(args.experiment_id, args.run_id)
    print(json.dumps({
        "status": result["summary"]["status"],
        "phase_root": result["phase_root"],
        "wall_s": result["summary"]["wall_s"],
        "control_stability": result["summary"]["control_stability"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
