"""V3-5 C：Dense+Reranker质量优先的候选深度与距离阈值扫描。

本入口复用A阶段复制的隔离索引，只执行development 72题，不调用生成模型，
不修改生产默认值。Hybrid与Dense基准均在同一GPU进程首尾重复；先扫描
Dense K，再按确定性质量门槛选一个K扫描distance阈值。
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from eval.crud_rag_1000.v3_1 import run_phase_b_mode_ablation as phase_b
from eval.crud_rag_1000.v3_1 import tuning_runner as runner


DENSE_K_VALUES = (10, 15, 30, 50)
DISTANCE_VALUES = (0.55, 0.60, 0.65, 0.67, 0.70, 0.75)
BASELINE_DENSE_K = 15
BASELINE_DISTANCE = 0.67
HARD_QUALITY_METRICS = (
    "document_hit_rate",
    "document_recall",
    "all_gold_docs_recalled_rate",
    "multi_document_complete_recall_rate",
    "evidence_coverage",
)


def _dense_config(dense_k: int, distance: float) -> dict[str, Any]:
    payload = deepcopy(runner.BASELINE_CONFIG)
    payload.update({
        "mode": "dense",
        "reranker_enabled": True,
        "dense_candidate_k": dense_k,
        "dense_distance_threshold": distance,
    })
    return runner._validated_config_payload(payload)


def _hybrid_control_config() -> dict[str, Any]:
    return runner._validated_config_payload(deepcopy(runner.BASELINE_CONFIG))


def _config_name(dense_k: int, distance: float) -> str:
    return f"dense-k{dense_k}-d{int(round(distance * 100)):03d}"


def _stage_candidates(row: dict[str, Any], stage: str) -> list[dict[str, Any]]:
    if stage == "selected":
        return row["retrieval"]
    stages = {
        item["name"]: item["candidates"]
        for item in row["trace"]["stages"]
    }
    if stage == "dense_raw":
        return stages.get("dense", [])
    if stage == "dense_eligible":
        return [item for item in stages.get("dense", []) if item.get("eligible")]
    return stages.get(stage, [])


def _orders(rows: list[dict[str, Any]], stage: str) -> dict[str, list[str]]:
    return {
        row["id"]: [item["chunk_id"] for item in _stage_candidates(row, stage)]
        for row in rows
    }


def _stage_signature(row: dict[str, Any], stage: str) -> list[tuple[Any, ...]]:
    fields = (
        "chunk_id", "rank", "dense_rank", "bm25_rank", "distance",
        "bm25_score", "rrf_score", "reranker_score", "eligible",
        "filtered_reason", "selected",
    )
    return [tuple(item.get(field) for field in fields) for item in _stage_candidates(row, stage)]


def _control_stability(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    stages: tuple[str, ...],
) -> dict[str, Any]:
    if [row["id"] for row in before] != [row["id"] for row in after]:
        raise RuntimeError("control前后题目ID或顺序不一致")
    differences: dict[str, list[str]] = {}
    matches: dict[str, int] = {}
    exact_differences: dict[str, list[str]] = {}
    exact_matches: dict[str, int] = {}
    for stage in stages:
        left = _orders(before, stage)
        right = _orders(after, stage)
        differences[stage] = [qid for qid in left if left[qid] != right[qid]]
        matches[stage] = len(left) - len(differences[stage])
        exact_differences[stage] = [
            left_row["id"]
            for left_row, right_row in zip(before, after)
            if _stage_signature(left_row, stage) != _stage_signature(right_row, stage)
        ]
        exact_matches[stage] = len(before) - len(exact_differences[stage])
    return {
        "question_count": len(before),
        "stage_order_identical_count": matches,
        "stage_order_difference_ids": differences,
        "stage_exact_identical_count": exact_matches,
        "stage_exact_difference_ids": exact_differences,
        "passed": (
            all(not value for value in differences.values())
            and all(not value for value in exact_differences.values())
        ),
    }


def _candidate_counts(rows: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    result: dict[str, dict[str, float | int]] = {}
    for stage in ("dense_raw", "dense_eligible", "reranker", "selected"):
        values = [len(_stage_candidates(row, stage)) for row in rows]
        result[stage] = {
            "min": min(values),
            "max": max(values),
            "mean": round(statistics.mean(values), 4),
        }
    return result


def _validate_trace(row: dict[str, Any], config: dict[str, Any]) -> None:
    trace = row["trace"]
    mode = config["mode"]
    expected_stages = (
        ["dense", "reranker", "selected"]
        if mode == "dense"
        else ["dense", "bm25", "rrf", "reranker", "selected"]
    )
    actual_stages = [item["name"] for item in trace["stages"]]
    if actual_stages != expected_stages:
        raise RuntimeError(
            f"{row['id']} Trace阶段异常：expected={expected_stages}, actual={actual_stages}"
        )
    if trace.get("retrieval_mode") != mode or trace.get("reranker_enabled") is not True:
        raise RuntimeError(f"{row['id']} Trace模式或Reranker开关异常")
    dense_raw = _stage_candidates(row, "dense_raw")
    dense_eligible = _stage_candidates(row, "dense_eligible")
    if len(dense_raw) != config["dense_candidate_k"]:
        raise RuntimeError(f"{row['id']} Dense实际请求结果数与K不符")
    if any(item.get("distance") is None for item in dense_raw):
        raise RuntimeError(f"{row['id']} Dense候选缺少distance")
    expected_eligible = [
        item for item in dense_raw
        if float(item["distance"]) <= float(config["dense_distance_threshold"])
    ]
    if [item["chunk_id"] for item in dense_eligible] != [item["chunk_id"] for item in expected_eligible]:
        raise RuntimeError(f"{row['id']} Dense eligible与阈值过滤不一致")
    preceding = dense_eligible if mode == "dense" else _stage_candidates(row, "rrf")
    reranker = _stage_candidates(row, "reranker")
    expected_reranker_count = min(config["reranker_candidate_k"], len(preceding))
    if len(reranker) != expected_reranker_count:
        raise RuntimeError(f"{row['id']} Reranker实际输入数异常")
    if set(item["chunk_id"] for item in reranker) != set(
        item["chunk_id"] for item in preceding[:config["reranker_candidate_k"]]
    ):
        raise RuntimeError(f"{row['id']} Reranker输入不来自前级候选")
    if any(item.get("reranker_score") is None for item in reranker):
        raise RuntimeError(f"{row['id']} Reranker候选缺少分数")
    selected = _stage_candidates(row, "selected")
    if [item["chunk_id"] for item in selected] != [item["chunk_id"] for item in row["retrieval"]]:
        raise RuntimeError(f"{row['id']} selected阶段与业务结果不一致")
    timings = trace.get("timings_ms", {})
    for key in ("query_embedding", "dense_retrieval", "rerank", "total"):
        if key not in timings or timings[key] is None:
            raise RuntimeError(f"{row['id']} Trace缺少耗时字段{key}")
    if trace.get("llm") is not None:
        raise RuntimeError(f"{row['id']} retrieve_only意外包含LLM Trace")


def _quality_gate(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    comparison: dict[str, Any],
) -> dict[str, Any]:
    metric_checks = {
        metric: float(candidate[metric]) + 1e-12 >= float(baseline[metric])
        for metric in HARD_QUALITY_METRICS
    }
    no_question_loss = not (
        comparison["answerable_regressed_ids"]
        or comparison["answerable_mixed_ids"]
    )
    return {
        "metric_not_below_dense_baseline": metric_checks,
        "no_answerable_regression_or_mixed_loss": no_question_loss,
        "passed": all(metric_checks.values()) and no_question_loss,
    }


def _quality_score(candidate: dict[str, Any], dense_k: int) -> tuple[float, ...]:
    return (
        float(candidate["all_gold_docs_recalled_rate"]),
        float(candidate["evidence_coverage"]),
        float(candidate["document_recall"]),
        float(candidate["multi_document_complete_recall_rate"]),
        float(candidate["document_hit_rate"]),
        float(candidate["document_mrr"]),
        -float(dense_k),
    )


def _select_k(k_evaluations: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [item for item in k_evaluations if item["quality_gate"]["passed"]]
    if not eligible:
        raise RuntimeError("Dense K基准自身未通过质量门槛，C协议或评分异常")
    winner = max(
        eligible,
        key=lambda item: _quality_score(item["candidate_result"], item["dense_k"]),
    )
    return {
        "selected_dense_k": winner["dense_k"],
        "selected_configuration": winner["configuration"],
        "selection_order": [
            "all_gold_docs_recalled_rate",
            "evidence_coverage",
            "document_recall",
            "multi_document_complete_recall_rate",
            "document_hit_rate",
            "document_mrr",
            "smaller_dense_k_on_quality_tie",
        ],
        "eligible_configurations": [item["configuration"] for item in eligible],
    }


def _select_final(evaluations: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [item for item in evaluations if item["quality_gate"]["passed"]]
    if not eligible:
        raise RuntimeError("阈值扫描没有任何配置通过，基准配置应始终在候选中")
    winner = max(
        eligible,
        key=lambda item: (
            _quality_score(item["candidate_result"], item["dense_k"]),
            -abs(float(item["distance"]) - BASELINE_DISTANCE),
        ),
    )
    baseline_quality = next(
        item["candidate_result"]
        for item in evaluations
        if item["dense_k"] == BASELINE_DENSE_K
        and abs(item["distance"] - BASELINE_DISTANCE) < 1e-9
    )
    quality_improved = any(
        float(winner["candidate_result"][metric]) > float(baseline_quality[metric]) + 1e-12
        for metric in HARD_QUALITY_METRICS
    )
    return {
        "selected_configuration": winner["configuration"],
        "selected_dense_k": winner["dense_k"],
        "selected_distance": winner["distance"],
        "quality_improved_over_dense_baseline": quality_improved,
        "recommendation": "advance_selected_to_D" if quality_improved else "retain_or_tie_break_candidate_for_D",
        "eligible_configurations": [item["configuration"] for item in eligible],
    }


def _render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# V3-5 C Dense质量优先扫描结果",
        "",
        f"- Run：`{payload['run_id']}`",
        "- 范围：development 72题（60可回答+12不可回答）",
        "- 固定项：Dense+Reranker、Reranker K15、Top5、每文档最多3块",
        "- 执行：同一GPU进程；Hybrid与Dense基准均首尾复验；无生成模型调用",
        "",
        "## 配置汇总",
        "",
        "| 配置 | Dense K | distance | Hit | Recall | MRR | all-gold | 多文档完整 | Evidence | 不可回答非空 | 门槛 | mean(s) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|",
    ]
    for row in payload["dense_evaluations"]:
        quality = row["quality"]
        lines.append(
            "| " + " | ".join([
                row["configuration"],
                str(row["dense_k"]),
                f"{row['distance']:.2f}",
                f"{quality['document_hit_rate']:.4f}",
                f"{quality['document_recall']:.4f}",
                f"{quality['document_mrr']:.4f}",
                f"{quality['all_gold_docs_recalled_rate']:.4f}",
                f"{quality['multi_document_complete_recall_rate']:.4f}",
                f"{quality['evidence_coverage']:.4f}",
                f"{quality['unanswerable_llm_entry_rate']:.4f}",
                "通过" if row["quality_gate"]["passed"] else "不通过",
                f"{row['latency']['mean']:.4f}",
            ]) + " |"
        )
    selection = payload["final_selection"]
    lines += [
        "",
        "## 选择结果",
        "",
        f"- C1选择Dense K：{payload['k_selection']['selected_dense_k']}",
        f"- C最终候选：`{selection['selected_configuration']}`（K={selection['selected_dense_k']}，distance={selection['selected_distance']:.2f}）",
        f"- 相对Dense基准硬质量指标有提升：{selection['quality_improved_over_dense_baseline']}",
        "- 本阶段不修改生产默认值，不运行D，不实现拒答门控。",
        "",
        "## 控制与边界",
        "",
        f"- Hybrid control首尾稳定：{payload['control_stability']['hybrid']['passed']}",
        f"- Dense baseline首尾稳定：{payload['control_stability']['dense']['passed']}",
        f"- Embedding/Reranker实际设备：{payload['runtime']['runtime']['embedding']['actual_device']} / {payload['runtime']['runtime']['reranker']['actual_device']}",
        f"- LLM模块导入：{payload['llm_module_imported']}；出站连接：{len(payload['outbound_ip_connections'])}",
        "- 单次顺序耗时仅作资源记录，正式性能结论留到F阶段。",
        "",
    ]
    return "\n".join(lines)


def run_phase_c(experiment_id: str, run_id: str) -> dict[str, Any]:
    experiment, protocol = runner._load_protocol(experiment_id)
    storage_before = runner._validate_experiment_storage(experiment, protocol)
    phase_b_verification = (
        experiment / "phase-b" / "v35-b-mode-ablation-20260829"
        / "phase-b-final-verification.json"
    )
    if not phase_b_verification.is_file() or runner._load_json(phase_b_verification).get("status") != "passed":
        raise RuntimeError("B阶段最终验收未通过，不能进入C")

    run_id = runner._safe_name(run_id, "run_id")
    phase_root = runner._assert_within(experiment / "phase-c" / run_id, experiment)
    phase_root.mkdir(parents=True, exist_ok=False)
    (phase_root / "configs").mkdir()
    (phase_root / "passes").mkdir()

    dataset = runner._dataset()
    if runner._sha256_file(runner.DATASET_PATH) != protocol["dataset"]["sha256"]:
        raise RuntimeError("题库hash与A协议不一致")
    questions = runner._split_questions(dataset, "development")
    if [row["id"] for row in questions] != protocol["splits"]["development"]:
        raise RuntimeError("development题目ID与A协议不一致")

    phase_protocol = {
        "schema_version": 1,
        "phase": "V3-5-C",
        "status": "running",
        "run_id": run_id,
        "created_at": runner._now(),
        "parent_experiment_id": experiment_id,
        "parent_protocol_identity": protocol["protocol_identity"],
        "dataset_sha256": protocol["dataset"]["sha256"],
        "storage_identity": protocol["source"]["storage_identity"],
        "question_count": len(questions),
        "route": "quality_first_dense_reranker",
        "candidate_grid": {
            "dense_k": list(DENSE_K_VALUES),
            "dense_distance": list(DISTANCE_VALUES),
            "maximum_unique_dense_configurations": 9,
            "threshold_k_selection": "deterministic_quality_gate_after_k_sweep",
        },
        "fixed_parameters": {
            "mode": "dense",
            "reranker_enabled": True,
            "reranker_candidate_k": 15,
            "final_top_k": 5,
            "max_chunks_per_document": 3,
            "bm25_and_rrf": "not_scanned_and_inactive_in_dense_mode",
        },
        "control_strategy": "same_process_hybrid_and_dense_pre_post",
        "selection_rule": {
            "hard_metrics_not_below_dense_baseline": list(HARD_QUALITY_METRICS),
            "no_answerable_regression_or_mixed_loss": True,
            "priority": [
                "all_gold", "evidence", "recall", "multi_document_complete",
                "hit", "mrr", "smaller_dense_k_on_quality_tie",
            ],
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
            "no D-stage reranker or final context sweep",
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
    hybrid = _hybrid_control_config()
    dense_baseline = _dense_config(BASELINE_DENSE_K, BASELINE_DISTANCE)
    warmup = adapters.retrieve_only(
        questions[0]["question"],
        include_trace=True,
        runtime_config=runner._runtime_config(hybrid),
    )
    if warmup["trace"]["effective_config"] != runner._effective_config(hybrid):
        raise RuntimeError("C预热control参数未实际生效")
    runner._write_json_exclusive(phase_root / "warmup.json", {
        "id": questions[0]["id"],
        "retrieval_s": warmup["retrieval_s"],
        "effective_config": warmup["trace"]["effective_config"],
        "not_in_quality_metrics": True,
    })

    config_records: dict[str, dict[str, Any]] = {}
    config_ids: dict[str, str] = {}

    def register(name: str, config: dict[str, Any]) -> None:
        config_id = runner._config_id(config)
        previous = config_ids.get(config_id)
        if previous:
            config_records[name] = config_records[previous]
            return
        record = {
            "name": name,
            "config_id": config_id,
            "retrieval": config,
            "effective_config": runner._effective_config(config),
            "derived": {
                "fusion_pool_k": (
                    runner._fusion_pool_k(config) if config["mode"] == "hybrid" else None
                ),
                "fusion_pool_k_applicable": config["mode"] == "hybrid",
                "actual_dense_request_k": config["dense_candidate_k"],
                "reranker_input_cap": config["reranker_candidate_k"],
            },
        }
        config_ids[config_id] = name
        config_records[name] = record
        runner._write_json_exclusive(phase_root / "configs" / f"{name}.json", record)

    register("hybrid-control", hybrid)
    register(_config_name(BASELINE_DENSE_K, BASELINE_DISTANCE), dense_baseline)

    pass_rows: dict[str, list[dict[str, Any]]] = {}
    pass_candidates: dict[str, dict[str, Any]] = {}
    pass_latencies: dict[str, dict[str, Any]] = {}
    actual_schedule: list[dict[str, str]] = []

    def execute(pass_name: str, config_name: str, config: dict[str, Any]) -> None:
        register(config_name, config)
        actual_schedule.append({"pass": pass_name, "configuration": config_name})
        print(f"[C {len(actual_schedule):02d}] {pass_name}", flush=True)
        rows: list[dict[str, Any]] = []
        rows_path = phase_root / "passes" / f"{pass_name}.jsonl"
        runtime_config = runner._runtime_config(config)
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
                    "config_id": runner._config_id(config),
                    "retrieval": result["retrieval"],
                    "retrieval_s": result["retrieval_s"],
                    "trace": trace,
                }
                _validate_trace(row, config)
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
                "config_id": runner._config_id(config),
                "question_count": len(rows),
                "quality": phase_b._compact_quality(candidate),
                "stage_quality": phase_b._stage_quality(candidate),
                "candidate_counts": _candidate_counts(rows),
                "latency": pass_latencies[pass_name],
                "candidate_result": candidate,
            },
        )

    execute("01-hybrid-control-pre", "hybrid-control", hybrid)
    baseline_name = _config_name(BASELINE_DENSE_K, BASELINE_DISTANCE)
    execute("02-dense-baseline-pre", baseline_name, dense_baseline)
    k_passes: dict[int, str] = {BASELINE_DENSE_K: "02-dense-baseline-pre"}
    for number, dense_k in enumerate((10, 30, 50), 3):
        name = _config_name(dense_k, BASELINE_DISTANCE)
        pass_name = f"{number:02d}-{name}"
        execute(pass_name, name, _dense_config(dense_k, BASELINE_DISTANCE))
        k_passes[dense_k] = pass_name

    baseline_candidate = pass_candidates["02-dense-baseline-pre"]
    baseline_rows = pass_rows["02-dense-baseline-pre"]
    k_evaluations: list[dict[str, Any]] = []
    for dense_k in DENSE_K_VALUES:
        pass_name = k_passes[dense_k]
        candidate = pass_candidates[pass_name]
        comparison = phase_b._compare_to_control(
            baseline_candidate, candidate, baseline_rows, pass_rows[pass_name]
        )
        k_evaluations.append({
            "configuration": _config_name(dense_k, BASELINE_DISTANCE),
            "pass": pass_name,
            "dense_k": dense_k,
            "distance": BASELINE_DISTANCE,
            "candidate_result": candidate,
            "comparison": comparison,
            "quality_gate": _quality_gate(baseline_candidate, candidate, comparison),
        })
    k_selection = _select_k(k_evaluations)
    selected_k = k_selection["selected_dense_k"]
    runner._write_json_exclusive(phase_root / "k-selection.json", {
        **k_selection,
        "evaluations": [
            {key: value for key, value in item.items() if key != "candidate_result"}
            for item in k_evaluations
        ],
    })
    print(f"[C selection] Dense K={selected_k}", flush=True)

    config_pass_by_id = {
        runner._config_id(_dense_config(k, BASELINE_DISTANCE)): pass_name
        for k, pass_name in k_passes.items()
    }
    for distance in DISTANCE_VALUES:
        config = _dense_config(selected_k, distance)
        config_id = runner._config_id(config)
        if config_id in config_pass_by_id:
            continue
        name = _config_name(selected_k, distance)
        pass_name = f"{len(actual_schedule) + 1:02d}-{name}"
        execute(pass_name, name, config)
        config_pass_by_id[config_id] = pass_name

    execute("11-dense-baseline-post", baseline_name, dense_baseline)
    execute("12-hybrid-control-post", "hybrid-control", hybrid)

    runtime = adapters.get_run_config()
    for component in ("embedding", "reranker"):
        status = runtime["runtime"][component]
        if not (status["loaded"] and status["verified"] and status["actual_device"] == "cuda:0"):
            raise RuntimeError(f"C阶段{component}未在cuda:0真实运行")
    if "backend.services.llm" in sys.modules:
        raise RuntimeError("C阶段意外导入LLM模块")
    if blocked:
        raise RuntimeError("C阶段出现出站网络尝试")

    hybrid_stability = _control_stability(
        pass_rows["01-hybrid-control-pre"],
        pass_rows["12-hybrid-control-post"],
        ("dense_raw", "dense_eligible", "bm25", "rrf", "reranker", "selected"),
    )
    dense_stability = _control_stability(
        baseline_rows,
        pass_rows["11-dense-baseline-post"],
        ("dense_raw", "dense_eligible", "reranker", "selected"),
    )
    for stability, first, last in (
        (hybrid_stability, "01-hybrid-control-pre", "12-hybrid-control-post"),
        (dense_stability, "02-dense-baseline-pre", "11-dense-baseline-post"),
    ):
        stability["quality_exact_match"] = (
            phase_b._compact_quality(pass_candidates[first])
            == phase_b._compact_quality(pass_candidates[last])
        )
        stability["passed"] = stability["passed"] and stability["quality_exact_match"]
    if not hybrid_stability["passed"] or not dense_stability["passed"]:
        raise RuntimeError("C阶段control首尾不稳定，拒绝解释参数收益")

    dense_evaluations: list[dict[str, Any]] = []
    seen_config_ids: set[str] = set()
    dense_config_names = [
        _config_name(k, BASELINE_DISTANCE) for k in DENSE_K_VALUES
    ] + [
        _config_name(selected_k, distance) for distance in DISTANCE_VALUES
    ]
    for name in dense_config_names:
        record = config_records[name]
        config_id = record["config_id"]
        if config_id in seen_config_ids:
            continue
        seen_config_ids.add(config_id)
        config = record["retrieval"]
        pass_name = config_pass_by_id[config_id]
        candidate = pass_candidates[pass_name]
        comparison = phase_b._compare_to_control(
            baseline_candidate, candidate, baseline_rows, pass_rows[pass_name]
        )
        dense_evaluations.append({
            "configuration": name,
            "pass": pass_name,
            "config_id": config_id,
            "dense_k": config["dense_candidate_k"],
            "distance": config["dense_distance_threshold"],
            "quality": phase_b._compact_quality(candidate),
            "stage_quality": phase_b._stage_quality(candidate),
            "candidate_counts": _candidate_counts(pass_rows[pass_name]),
            "latency": pass_latencies[pass_name],
            "comparison": comparison,
            "quality_gate": _quality_gate(baseline_candidate, candidate, comparison),
            "candidate_result": candidate,
        })
    final_selection = _select_final(dense_evaluations)
    storage_after = runner._validate_experiment_storage(experiment, protocol)
    summary = {
        "schema_version": 1,
        "phase": "V3-5-C",
        "status": "passed",
        "run_id": run_id,
        "parent_experiment_id": experiment_id,
        "completed_at": runner._now(),
        "wall_s": round(time.perf_counter() - started, 3),
        "question_count": len(questions),
        "unique_dense_configuration_count": len(dense_evaluations),
        "unique_total_configuration_count": len(seen_config_ids) + 1,
        "quality_pass_count": len(actual_schedule),
        "quality_query_count": len(questions) * len(actual_schedule),
        "warmup_query_count": 1,
        "real_retrieval_query_count": len(questions) * len(actual_schedule) + 1,
        "actual_schedule": actual_schedule,
        "k_selection": k_selection,
        "dense_evaluations": [
            {key: value for key, value in item.items() if key != "candidate_result"}
            for item in dense_evaluations
        ],
        "final_selection": final_selection,
        "control_stability": {"hybrid": hybrid_stability, "dense": dense_stability},
        "runtime": runtime,
        "llm_module_imported": False,
        "outbound_ip_connections": blocked,
        "storage_before": storage_before,
        "storage_after": storage_after,
        "limits": phase_protocol["limits"],
    }
    runner._write_json_exclusive(phase_root / "phase-c-summary.json", summary)
    (phase_root / "phase-c-report.md").write_text(_render_report(summary), encoding="utf-8")
    runner._write_json_exclusive(phase_root / "protocol-completed.json", {
        **phase_protocol,
        "status": "passed",
        "completed_at": summary["completed_at"],
        "actual_schedule": actual_schedule,
        "selected_dense_k_for_threshold_sweep": selected_k,
        "final_selection": final_selection,
    })
    return {"phase_root": str(phase_root), "summary": summary}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-id", default="v35-a-baseline-20260829")
    parser.add_argument("--run-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_phase_c(args.experiment_id, args.run_id)
    summary = result["summary"]
    print(json.dumps({
        "status": summary["status"],
        "phase_root": result["phase_root"],
        "wall_s": summary["wall_s"],
        "k_selection": summary["k_selection"],
        "final_selection": summary["final_selection"],
        "control_stability": summary["control_stability"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
