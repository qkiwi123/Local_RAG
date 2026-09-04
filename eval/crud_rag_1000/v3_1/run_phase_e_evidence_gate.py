"""V3-5 E：基于D阶段固定Trace的离线多信号证据充分性门控。

本入口只读取D阶段Dense20/Reranker20 development 72题及Top5/Top10上下文重放，
不会重新执行检索、加载模型、调用LLM、修改生产默认值或改写D阶段产物。正式选择先要求
0道可回答误拒答、至少9/12不可回答拦截和扰动稳定，再按拦截数及保守平局规则选择。
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import itertools
import json
import math
from pathlib import Path
import statistics
import sys
import time
from typing import Any, Iterable
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from eval.crud_rag_1000.v3_1 import run_phase_b_mode_ablation as phase_b
from eval.crud_rag_1000.v3_1 import run_phase_b2_hybrid_bm25_sweep as phase_b2
from eval.crud_rag_1000.v3_1 import run_phase_d_rerank_context_sweep as phase_d
from eval.crud_rag_1000.v3_1 import tuning_runner as runner


DEFAULT_EXPERIMENT_ID = "v35-a-baseline-20260829"
DEFAULT_PHASE_D_RUN_ID = "v35-d-rerank-context-quality-20260830"
SOURCE_PASS = "05-dense-k20-rerank-k20"
SOURCE_REAL_CONFIGURATION = "dense-k20-rerank-k20"
CONTEXT_CONFIGURATIONS = ("top10-doccap2", "top5-doccap2")

SCORE_EPSILON = 0.02
DISTANCE_EPSILON = 0.005
MIN_UNANSWERABLE_BLOCKED = 9
STRICT_MAX_FALSE_REJECTS = 0
EXPLORATORY_MAX_FALSE_REJECTS = 1


def _single_policy(threshold: float) -> dict[str, Any]:
    label = str(threshold).replace("-", "m").replace(".", "p")
    return {
        "name": f"single-s1-{label}",
        "kind": "single_score",
        "parameters": {"score_threshold": threshold},
        "signal_count": 1,
    }


def _multi_policy(
    name: str,
    low_score: float,
    high_score: float,
    dense_best_weak: float,
    top1_dense_weak: float,
) -> dict[str, Any]:
    return {
        "name": name,
        "kind": "multi_signal",
        "parameters": {
            "low_score": low_score,
            "high_score": high_score,
            "dense_best_weak": dense_best_weak,
            "top1_dense_weak": top1_dense_weak,
        },
        "signal_count": 3,
    }


POLICY_SPECS: tuple[dict[str, Any], ...] = (
    {"name": "gate-off", "kind": "off", "parameters": {}, "signal_count": 0},
    *(_single_policy(value) for value in (-4.0, -3.0, -2.5, -2.0, -1.0, 0.0, 0.5)),
    _multi_policy("M1", -3.0, 0.5, 0.52, 0.55),
    _multi_policy("M2", -2.5, 0.5, 0.52, 0.55),
    _multi_policy("M3", -2.0, 0.5, 0.52, 0.55),
    _multi_policy("M4", -2.5, 0.0, 0.52, 0.55),
    _multi_policy("M5", -2.5, 1.0, 0.52, 0.55),
    _multi_policy("M6", -2.5, 0.5, 0.50, 0.52),
    _multi_policy("M7", -2.5, 0.5, 0.54, 0.58),
    _multi_policy("M8", -2.5, 0.5, 0.48, 0.50),
)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"JSONL解析失败：{path}:{line_number}: {exc}") from exc
    return rows


def _stage_candidates(row: dict[str, Any], stage: str) -> list[dict[str, Any]]:
    stages = (row.get("trace") or {}).get("stages") or []
    matches = [item for item in stages if item.get("name") == stage]
    if len(matches) != 1:
        raise RuntimeError(f"{row.get('id')} 缺少或重复Trace阶段：{stage}")
    candidates = matches[0].get("candidates") or []
    if not candidates:
        raise RuntimeError(f"{row.get('id')} Trace阶段为空：{stage}")
    return candidates


def _extract_features(row: dict[str, Any]) -> dict[str, float | int]:
    reranker = _stage_candidates(row, "reranker")
    dense = _stage_candidates(row, "dense")
    if len(reranker) < 5:
        raise RuntimeError(f"{row.get('id')} Reranker候选少于5，无法计算E信号")
    scores = [float(item["reranker_score"]) for item in reranker]
    eligible_dense_distances = [
        float(item["distance"])
        for item in dense
        if item.get("eligible") is True and item.get("distance") is not None
    ]
    if not eligible_dense_distances:
        raise RuntimeError(f"{row.get('id')} 没有Dense eligible距离")
    top1 = reranker[0]
    if top1.get("distance") is None or top1.get("dense_rank") is None:
        raise RuntimeError(f"{row.get('id')} Reranker Top1缺少Dense来源属性")
    return {
        "reranker_top1": scores[0],
        "reranker_top2": scores[1],
        "reranker_top3": scores[2],
        "reranker_margin_1_2": scores[0] - scores[1],
        "reranker_top3_mean": statistics.mean(scores[:3]),
        "top5_score_ge_0_count": sum(score >= 0.0 for score in scores[:5]),
        "top5_score_ge_m2_count": sum(score >= -2.0 for score in scores[:5]),
        "dense_best_distance": min(eligible_dense_distances),
        "reranker_top1_dense_distance": float(top1["distance"]),
        "reranker_top1_dense_rank": int(top1["dense_rank"]),
    }


def _decision(policy: dict[str, Any], features: dict[str, float | int]) -> dict[str, str]:
    kind = policy["kind"]
    top1 = float(features["reranker_top1"])
    if kind == "off":
        return {"decision": "allow", "reason": "gate_off"}
    if kind == "single_score":
        threshold = float(policy["parameters"]["score_threshold"])
        if top1 < threshold:
            return {"decision": "reject", "reason": "single_score_below_threshold"}
        return {"decision": "allow", "reason": "single_score_at_or_above_threshold"}
    if kind != "multi_signal":
        raise ValueError(f"未知门控策略类型：{kind}")
    parameters = policy["parameters"]
    if top1 < float(parameters["low_score"]):
        return {"decision": "reject", "reason": "reranker_top1_below_low"}
    if top1 >= float(parameters["high_score"]):
        return {"decision": "allow", "reason": "reranker_top1_at_or_above_high"}
    dense_weak = (
        float(features["dense_best_distance"]) > float(parameters["dense_best_weak"])
        and float(features["reranker_top1_dense_distance"])
        > float(parameters["top1_dense_weak"])
    )
    if dense_weak:
        return {"decision": "reject", "reason": "gray_both_dense_signals_weak"}
    return {"decision": "allow", "reason": "gray_quality_first_allow"}


def _perturbations(features: dict[str, float | int]) -> Iterable[dict[str, float | int]]:
    for score_delta, dense_best_delta, top1_dense_delta in itertools.product(
        (-SCORE_EPSILON, SCORE_EPSILON),
        (-DISTANCE_EPSILON, DISTANCE_EPSILON),
        (-DISTANCE_EPSILON, DISTANCE_EPSILON),
    ):
        varied = dict(features)
        varied["reranker_top1"] = float(features["reranker_top1"]) + score_delta
        varied["dense_best_distance"] = (
            float(features["dense_best_distance"]) + dense_best_delta
        )
        varied["reranker_top1_dense_distance"] = (
            float(features["reranker_top1_dense_distance"]) + top1_dense_delta
        )
        yield varied


def _decision_robustness(
    policy: dict[str, Any], features: dict[str, float | int]
) -> dict[str, Any]:
    nominal = _decision(policy, features)
    varied_decisions = {
        _decision(policy, varied)["decision"] for varied in _perturbations(features)
    }
    return {
        **nominal,
        "perturbation_stable": varied_decisions == {nominal["decision"]},
        "perturbed_decisions": sorted(varied_decisions),
    }


def _evaluate_policy(
    policy: dict[str, Any],
    questions: list[dict[str, Any]],
    features_by_id: dict[str, dict[str, float | int]],
) -> dict[str, Any]:
    decisions: dict[str, dict[str, Any]] = {}
    allowed_answerable: list[str] = []
    rejected_answerable: list[str] = []
    rejected_unanswerable: list[str] = []
    allowed_unanswerable: list[str] = []
    unstable_ids: list[str] = []
    reasons: Counter[str] = Counter()
    for question in questions:
        question_id = question["id"]
        result = _decision_robustness(policy, features_by_id[question_id])
        decisions[question_id] = result
        reasons[result["reason"]] += 1
        if not result["perturbation_stable"]:
            unstable_ids.append(question_id)
        rejected = result["decision"] == "reject"
        if question["answerable"]:
            (rejected_answerable if rejected else allowed_answerable).append(question_id)
        else:
            (rejected_unanswerable if rejected else allowed_unanswerable).append(question_id)
    strict_eligible = (
        policy["kind"] != "off"
        and len(rejected_answerable) <= STRICT_MAX_FALSE_REJECTS
        and len(rejected_unanswerable) >= MIN_UNANSWERABLE_BLOCKED
        and not unstable_ids
    )
    exploratory_eligible = (
        policy["kind"] != "off"
        and len(rejected_answerable) <= EXPLORATORY_MAX_FALSE_REJECTS
        and len(rejected_unanswerable) >= MIN_UNANSWERABLE_BLOCKED
    )
    return {
        "name": policy["name"],
        "kind": policy["kind"],
        "parameters": deepcopy(policy["parameters"]),
        "signal_count": policy["signal_count"],
        "counts": {
            "allowed_answerable": len(allowed_answerable),
            "false_rejected_answerable": len(rejected_answerable),
            "blocked_unanswerable": len(rejected_unanswerable),
            "missed_unanswerable": len(allowed_unanswerable),
        },
        "ids": {
            "false_rejected_answerable": rejected_answerable,
            "blocked_unanswerable": rejected_unanswerable,
            "missed_unanswerable": allowed_unanswerable,
            "perturbation_unstable": unstable_ids,
        },
        "reason_counts": dict(sorted(reasons.items())),
        "robustness": {
            "score_delta": SCORE_EPSILON,
            "distance_delta": DISTANCE_EPSILON,
            "all_question_decisions_stable": not unstable_ids,
            "unstable_question_count": len(unstable_ids),
        },
        "strict_eligible": strict_eligible,
        "exploratory_eligible": exploratory_eligible,
        "decisions": decisions,
    }


def _selection_key(evaluation: dict[str, Any]) -> tuple[float, ...]:
    parameters = evaluation["parameters"]
    is_multi = evaluation["kind"] == "multi_signal"
    return (
        float(evaluation["counts"]["blocked_unanswerable"]),
        float(parameters.get("dense_best_weak", -math.inf)),
        float(parameters.get("top1_dense_weak", -math.inf)),
        -abs(float(parameters.get("high_score", 0.5)) - 0.5),
        -abs(float(parameters.get("low_score", -2.5)) + 2.5),
        1.0 if is_multi else 0.0,
        -float(evaluation["signal_count"]),
    )


def _select_policy(evaluations: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [item for item in evaluations if item["strict_eligible"]]
    if not eligible:
        raise RuntimeError("E阶段没有策略同时满足0误拒、9/12拦截和扰动稳定")
    winner = max(eligible, key=_selection_key)
    return {
        "selected_policy": winner["name"],
        "selected_kind": winner["kind"],
        "selected_parameters": deepcopy(winner["parameters"]),
        "eligible_policies": [item["name"] for item in eligible],
        "selection_order": [
            "zero_answerable_false_reject",
            "at_least_9_of_12_unanswerable_blocked",
            "all_decisions_stable_under_score_0.02_and_distance_0.005",
            "more_unanswerable_blocked",
            "more_conservative_dense_thresholds_on_equal_observed_result",
            "high_score_closer_to_0.5",
            "low_score_closer_to_-2.5",
            "multi_signal_over_single_on_remaining_tie",
        ],
        "not_a_weighted_score": True,
    }


def _quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def _signal_distribution(
    questions: list[dict[str, Any]],
    features_by_id: dict[str, dict[str, float | int]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for answerable, label in ((True, "answerable"), (False, "unanswerable")):
        ids = [question["id"] for question in questions if question["answerable"] is answerable]
        group: dict[str, Any] = {"question_count": len(ids), "signals": {}}
        for key in (
            "reranker_top1", "reranker_top2", "reranker_top3",
            "reranker_margin_1_2", "reranker_top3_mean", "dense_best_distance",
            "reranker_top1_dense_distance", "reranker_top1_dense_rank",
            "top5_score_ge_0_count", "top5_score_ge_m2_count",
        ):
            values = [float(features_by_id[question_id][key]) for question_id in ids]
            group["signals"][key] = {
                "min": min(values),
                "p10": _quantile(values, 0.10),
                "p25": _quantile(values, 0.25),
                "p50": _quantile(values, 0.50),
                "p75": _quantile(values, 0.75),
                "p90": _quantile(values, 0.90),
                "max": max(values),
            }
        result[label] = group
    return result


def _apply_gate(
    replay_rows: list[dict[str, Any]], decisions: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in replay_rows:
        decision = decisions[row["id"]]["decision"]
        result.append({
            "id": row["id"],
            "retrieval": [] if decision == "reject" else deepcopy(row["retrieval"]),
        })
    return result


def _context_binding(
    questions: list[dict[str, Any]],
    replay_payload: dict[str, Any],
    decisions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    rows = replay_payload["rows"]
    gated_rows = _apply_gate(rows, decisions)
    before = phase_d._build_candidate(
        questions, rows, candidate_k=int(replay_payload["final_top_k"])
    )
    after = phase_d._build_candidate(
        questions, gated_rows, candidate_k=int(replay_payload["final_top_k"])
    )
    comparison = phase_b._compare_to_control(before, after, rows, gated_rows)
    return {
        "configuration": replay_payload["configuration"],
        "source_real_configuration": replay_payload["source_real_configuration"],
        "final_top_k": replay_payload["final_top_k"],
        "max_chunks_per_document": replay_payload["max_chunks_per_document"],
        "quality_before_gate": phase_b._compact_quality(before),
        "quality_after_gate": phase_b._compact_quality(after),
        "answerable_regressed_ids": comparison["answerable_regressed_ids"],
        "answerable_mixed_ids": comparison["answerable_mixed_ids"],
        "unanswerable_nonempty_before": sum(
            bool(row["retrieval"])
            for row, question in zip(rows, questions)
            if not question["answerable"]
        ),
        "unanswerable_nonempty_after": sum(
            bool(row["retrieval"])
            for row, question in zip(gated_rows, questions)
            if not question["answerable"]
        ),
        "context_budget_before": phase_d._context_budget(rows),
        "context_budget_after": phase_d._context_budget(gated_rows),
    }


def _artifact_identity(path: Path) -> dict[str, Any]:
    return {"sha256": runner._sha256_file(path), "bytes": path.stat().st_size}


def _storage_facts(storage: dict[str, Any]) -> dict[str, Any]:
    databases = storage["database_checks"]
    return {
        "database_quick_check": databases["metadata.db"]["quick_check"],
        "chroma_quick_check": databases["chroma.sqlite3"]["quick_check"],
        "documents": databases["metadata.db"]["documents"],
        "document_units": databases["metadata.db"]["document_units"],
        "embeddings": databases["chroma.sqlite3"]["embeddings"],
    }


def _input_paths(d_root: Path) -> dict[str, Path]:
    return {
        "phase-d-final-verification.json": d_root / "phase-d-final-verification.json",
        "phase-d-summary.json": d_root / "phase-d-summary.json",
        f"passes/{SOURCE_PASS}.jsonl": d_root / "passes" / f"{SOURCE_PASS}.jsonl",
        "replays/top10-doccap2.json": d_root / "replays" / "top10-doccap2.json",
        "replays/top5-doccap2.json": d_root / "replays" / "top5-doccap2.json",
        "questions_v3_120q.json": runner.DATASET_PATH,
    }


def _validate_d_contract(d_root: Path, questions: list[dict[str, Any]]) -> dict[str, Any]:
    verification = runner._load_json(d_root / "phase-d-final-verification.json")
    summary = runner._load_json(d_root / "phase-d-summary.json")
    if verification.get("status") != "passed" or summary.get("status") != "passed":
        raise RuntimeError("D阶段最终验收未通过，不能执行E")
    reranker = summary.get("reranker_selection", {})
    context = summary.get("final_selection", {})
    if (
        reranker.get("selected_configuration") != SOURCE_REAL_CONFIGURATION
        or reranker.get("selected_dense_k") != 20
        or reranker.get("selected_reranker_k") != 20
    ):
        raise RuntimeError("D阶段真实预算不是Dense20/Reranker20")
    if (
        context.get("selected_quality_configuration") != "top10-doccap2"
        or context.get("selected_cost_balanced_configuration") != "top5-doccap2"
    ):
        raise RuntimeError("D阶段上下文候选与E预注册输入不一致")
    source_rows = _load_jsonl(d_root / "passes" / f"{SOURCE_PASS}.jsonl")
    expected_ids = [question["id"] for question in questions]
    if [row["id"] for row in source_rows] != expected_ids:
        raise RuntimeError("D阶段Dense20/Reranker20行顺序与development不一致")
    replay_payloads: dict[str, dict[str, Any]] = {}
    for name in CONTEXT_CONFIGURATIONS:
        payload = runner._load_json(d_root / "replays" / f"{name}.json")
        if (
            payload.get("configuration") != name
            or payload.get("source_real_configuration") != SOURCE_REAL_CONFIGURATION
            or [row["id"] for row in payload.get("rows", [])] != expected_ids
        ):
            raise RuntimeError(f"D阶段上下文重放契约不一致：{name}")
        replay_payloads[name] = payload
    return {
        "verification": verification,
        "summary": summary,
        "source_rows": source_rows,
        "replay_payloads": replay_payloads,
    }


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# V3-5 E 多信号证据充分性门控结果",
        "",
        f"- Run：`{summary['run_id']}`",
        "- 范围：development 72题（60可回答+12不可回答）",
        "- 方法：只读D阶段固定Trace的离线门控重放；0次检索、0次LLM调用",
        "",
        "## 策略矩阵",
        "",
        "| 策略 | 类型 | 可回答误拒 | 不可回答拦截 | 扰动不稳定 | 严格合格 |",
        "|---|---|---:|---:|---:|---|",
    ]
    for evaluation in summary["policy_evaluations"]:
        counts = evaluation["counts"]
        robustness = evaluation["robustness"]
        lines.append(
            f"| {evaluation['name']} | {evaluation['kind']} | "
            f"{counts['false_rejected_answerable']}/60 | "
            f"{counts['blocked_unanswerable']}/12 | "
            f"{robustness['unstable_question_count']} | "
            f"{'是' if evaluation['strict_eligible'] else '否'} |"
        )
    selection = summary["final_selection"]
    selected = next(
        item for item in summary["policy_evaluations"]
        if item["name"] == selection["selected_policy"]
    )
    lines += [
        "",
        "## 选择结果",
        "",
        f"- 选择：`{selection['selected_policy']}`，参数：`{json.dumps(selection['selected_parameters'], ensure_ascii=False)}`",
        f"- 可回答误拒：{selected['counts']['false_rejected_answerable']}/60",
        f"- 不可回答拦截：{selected['counts']['blocked_unanswerable']}/12",
        f"- 扰动不稳定：{selected['robustness']['unstable_question_count']}/72",
        "- 这是硬门槛和确定性平局规则，不是给M8降低主观权重。",
        "",
        "## 上下文绑定",
        "",
        "| 配置 | 门控前不可回答非空 | 门控后不可回答非空 | 可回答回归/混合 |",
        "|---|---:|---:|---:|",
    ]
    for binding in summary["context_bindings"]:
        lines.append(
            f"| {binding['configuration']} | {binding['unanswerable_nonempty_before']}/12 | "
            f"{binding['unanswerable_nonempty_after']}/12 | "
            f"{len(binding['answerable_regressed_ids'])}/{len(binding['answerable_mixed_ids'])} |"
        )
    lines += [
        "",
        "## 边界",
        "",
        "- validation/frozen_test未读取、未调参；数据集仍为draft。",
        "- Reranker分数是当前模型/精度/候选集下的相关性logit，不是概率。",
        "- E只交付离线策略，不代表业务门控、拒答文案或生产开关已经实现。",
        "- 下一阶段F及S7生产接入均需另行确认。",
        "",
    ]
    return "\n".join(lines)


def _render_conclusion(summary: dict[str, Any]) -> str:
    selection = summary["final_selection"]
    selected = next(
        item for item in summary["policy_evaluations"]
        if item["name"] == selection["selected_policy"]
    )
    m8 = next(item for item in summary["policy_evaluations"] if item["name"] == "M8")
    return "\n".join([
        "# V3-5 E 阶段结论",
        "",
        f"E在固定D阶段development Trace上选择`{selection['selected_policy']}`。",
        f"它在当前60道可回答题上误拒{selected['counts']['false_rejected_answerable']}道，"
        f"拦截{selected['counts']['blocked_unanswerable']}/12道不可回答题，且72题在"
        f"reranker ±{SCORE_EPSILON}、Dense distance ±{DISTANCE_EPSILON}扰动下决策不变。",
        "",
        f"M8表面拦截{m8['counts']['blocked_unanswerable']}/12，但有"
        f"{m8['robustness']['unstable_question_count']}道在预注册扰动下翻转，因此不满足严格资格；"
        "这不是权重降级，而是稳定性硬门槛淘汰。",
        "",
        "同一门控策略绑定Top10/配额2质量候选和Top5/配额2成本候选；门控信号来自共同的"
        "Dense20/Reranker20上游，所以两套上下文的拒答决定必须一致。E没有真实调用检索或模型，"
        "没有改生产默认值，也没有执行validation、frozen_test、F阶段或S7业务接入。",
        "",
    ])


def run_phase_e(
    experiment_id: str,
    phase_d_run_id: str,
    run_id: str,
) -> dict[str, Any]:
    experiment, protocol = runner._load_protocol(experiment_id)
    storage_before = runner._validate_experiment_storage(experiment, protocol)
    run_id = runner._safe_name(run_id, "run_id")
    phase_d_run_id = runner._safe_name(phase_d_run_id, "phase_d_run_id")
    d_root = runner._assert_within(experiment / "phase-d" / phase_d_run_id, experiment)
    phase_root = runner._assert_within(experiment / "phase-e" / run_id, experiment)
    phase_root.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    blocked: list[dict[str, Any]] = []
    runner._install_network_guard(blocked, started)

    dataset = runner._dataset()
    if runner._sha256_file(runner.DATASET_PATH) != protocol["dataset"]["sha256"]:
        raise RuntimeError("题库hash与A协议不一致")
    questions = runner._split_questions(dataset, "development")
    if [question["id"] for question in questions] != protocol["splits"]["development"]:
        raise RuntimeError("development题目ID与A协议不一致")
    if sum(question["answerable"] for question in questions) != 60:
        raise RuntimeError("E阶段development可回答题不是60道")
    if sum(not question["answerable"] for question in questions) != 12:
        raise RuntimeError("E阶段development不可回答题不是12道")

    contract = _validate_d_contract(d_root, questions)
    input_paths = _input_paths(d_root)
    input_before = {name: _artifact_identity(path) for name, path in input_paths.items()}
    phase_protocol = {
        "schema_version": 1,
        "phase": "V3-5-E",
        "status": "running",
        "run_id": run_id,
        "created_at": runner._now(),
        "parent_experiment_id": experiment_id,
        "source_phase_d_run_id": phase_d_run_id,
        "source_real_configuration": SOURCE_REAL_CONFIGURATION,
        "source_pass": SOURCE_PASS,
        "context_configurations": list(CONTEXT_CONFIGURATIONS),
        "dataset_sha256": protocol["dataset"]["sha256"],
        "question_count": len(questions),
        "policy_count": len(POLICY_SPECS),
        "policy_names": [policy["name"] for policy in POLICY_SPECS],
        "hard_acceptance": {
            "max_answerable_false_rejects": STRICT_MAX_FALSE_REJECTS,
            "min_unanswerable_blocked": MIN_UNANSWERABLE_BLOCKED,
            "all_decisions_perturbation_stable": True,
        },
        "perturbation": {
            "reranker_score_plus_minus": SCORE_EPSILON,
            "dense_distance_plus_minus": DISTANCE_EPSILON,
            "corners_per_question_policy": 8,
        },
        "input_artifacts": input_before,
        "runner_sha256": runner._sha256_file(Path(__file__).resolve()),
        "storage_identity": protocol["source"]["storage_identity"],
        "limits": [
            "development only",
            "offline replay only; zero retrieval/model/API calls",
            "validation and frozen_test not read",
            "D artifacts are read-only and not rewritten",
            "no production setting or business refusal gate change",
            "dataset remains draft",
            "F, G and S7 not executed",
        ],
    }
    runner._write_json_exclusive(phase_root / "protocol.json", phase_protocol)
    runner._write_json_exclusive(phase_root / "policies.json", {
        "schema_version": 1,
        "policy_count": len(POLICY_SPECS),
        "policies": POLICY_SPECS,
        "score_epsilon": SCORE_EPSILON,
        "distance_epsilon": DISTANCE_EPSILON,
    })

    source_rows = contract["source_rows"]
    features_by_id = {row["id"]: _extract_features(row) for row in source_rows}
    evaluations_full = [
        _evaluate_policy(policy, questions, features_by_id) for policy in POLICY_SPECS
    ]
    selection = _select_policy(evaluations_full)
    selected_evaluation = next(
        item for item in evaluations_full
        if item["name"] == selection["selected_policy"]
    )
    context_bindings = [
        _context_binding(
            questions,
            contract["replay_payloads"][name],
            selected_evaluation["decisions"],
        )
        for name in CONTEXT_CONFIGURATIONS
    ]
    if any(
        binding["answerable_regressed_ids"] or binding["answerable_mixed_ids"]
        for binding in context_bindings
    ):
        raise RuntimeError("所选门控导致可回答题上下文质量回归")
    if len({binding["unanswerable_nonempty_after"] for binding in context_bindings}) != 1:
        raise RuntimeError("Top5/Top10共享门控信号却产生不同拒答结果")

    replay_by_name = contract["replay_payloads"]
    with (phase_root / "per-question.jsonl").open("x", encoding="utf-8") as handle:
        question_by_id = {question["id"]: question for question in questions}
        for source_row in source_rows:
            question_id = source_row["id"]
            decisions = {
                evaluation["name"]: evaluation["decisions"][question_id]
                for evaluation in evaluations_full
            }
            selected_decision = decisions[selection["selected_policy"]]
            contexts: dict[str, Any] = {}
            for name in CONTEXT_CONFIGURATIONS:
                replay_row = next(
                    row for row in replay_by_name[name]["rows"] if row["id"] == question_id
                )
                before_ids = [item["chunk_id"] for item in replay_row["retrieval"]]
                contexts[name] = {
                    "before_chunk_ids": before_ids,
                    "after_chunk_ids": (
                        [] if selected_decision["decision"] == "reject" else before_ids
                    ),
                }
            payload = {
                "id": question_id,
                "split": question_by_id[question_id]["split"],
                "answerable": question_by_id[question_id]["answerable"],
                "category": question_by_id[question_id]["category"],
                "features": features_by_id[question_id],
                "policy_decisions": decisions,
                "selected_policy": selection["selected_policy"],
                "selected_decision": selected_decision,
                "contexts": contexts,
            }
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    input_after = {name: _artifact_identity(path) for name, path in input_paths.items()}
    if input_before != input_after:
        raise RuntimeError("E离线重放期间D输入或题库发生变化")
    storage_after = runner._validate_experiment_storage(experiment, protocol)
    if storage_before != storage_after:
        raise RuntimeError("E离线重放期间隔离或生产存储发生变化")
    if blocked:
        raise RuntimeError("E离线重放出现出站网络尝试")
    if "backend.services.llm" in sys.modules:
        raise RuntimeError("E离线重放意外导入LLM模块")
    runner._source_artifact_checks(
        runner._storage_manifest(
            runner.SOURCE_RUN / "chroma", runner.SOURCE_RUN / "metadata.db"
        )
    )
    production = phase_b2._production_snapshot_check()
    if production["files_changed"]:
        raise RuntimeError("E离线重放期间生产快照发生变化")

    evaluations = [
        {key: value for key, value in evaluation.items() if key != "decisions"}
        for evaluation in evaluations_full
    ]
    summary = {
        "schema_version": 1,
        "phase": "V3-5-E",
        "status": "passed",
        "run_id": run_id,
        "parent_experiment_id": experiment_id,
        "source_phase_d_run_id": phase_d_run_id,
        "completed_at": runner._now(),
        "wall_s": round(time.perf_counter() - started, 3),
        "question_count": len(questions),
        "answerable_count": 60,
        "unanswerable_count": 12,
        "policy_count": len(evaluations),
        "single_score_policy_count": sum(item["kind"] == "single_score" for item in evaluations),
        "multi_signal_policy_count": sum(item["kind"] == "multi_signal" for item in evaluations),
        "real_retrieval_query_count": 0,
        "llm_call_count": 0,
        "signal_distributions": _signal_distribution(questions, features_by_id),
        "policy_evaluations": evaluations,
        "final_selection": selection,
        "context_bindings": context_bindings,
        "input_artifacts_before": input_before,
        "input_artifacts_after": input_after,
        "input_artifacts_unchanged": input_before == input_after,
        "storage_before": storage_before,
        "storage_after": storage_after,
        "sealed_baseline_files_checked": len(
            runner._load_json(runner.SOURCE_AUDIT / "artifact-manifest.json")["files"]
        ),
        "production_snapshot": production,
        "llm_module_imported": False,
        "outbound_ip_connections": blocked,
        "limits": phase_protocol["limits"],
    }
    runner._write_json_exclusive(phase_root / "phase-e-summary.json", summary)
    (phase_root / "phase-e-report.md").write_text(
        _render_report(summary), encoding="utf-8"
    )
    (phase_root / "phase-e-conclusion.md").write_text(
        _render_conclusion(summary), encoding="utf-8"
    )
    runner._write_json_exclusive(phase_root / "protocol-completed.json", {
        **phase_protocol,
        "status": "passed",
        "completed_at": summary["completed_at"],
        "final_selection": selection,
        "context_bindings": [
            {
                "configuration": binding["configuration"],
                "unanswerable_nonempty_after": binding["unanswerable_nonempty_after"],
                "answerable_regression_count": len(binding["answerable_regressed_ids"]),
            }
            for binding in context_bindings
        ],
    })
    return {"phase_root": str(phase_root), "summary": summary}


def _junit_counts(path: Path) -> dict[str, int | float]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    if not suites:
        raise RuntimeError("JUnit中没有testsuite")
    return {
        "tests": sum(int(suite.attrib.get("tests", 0)) for suite in suites),
        "failures": sum(int(suite.attrib.get("failures", 0)) for suite in suites),
        "errors": sum(int(suite.attrib.get("errors", 0)) for suite in suites),
        "skipped": sum(int(suite.attrib.get("skipped", 0)) for suite in suites),
        "time_s": round(sum(float(suite.attrib.get("time", 0.0)) for suite in suites), 3),
    }


def verify_phase_e(
    experiment_id: str,
    run_id: str,
    junit_path: Path,
    expected_tests: int,
) -> dict[str, Any]:
    experiment, protocol = runner._load_protocol(experiment_id)
    run_id = runner._safe_name(run_id, "run_id")
    phase_root = runner._assert_within(experiment / "phase-e" / run_id, experiment)
    summary = runner._load_json(phase_root / "phase-e-summary.json")
    completed = runner._load_json(phase_root / "protocol-completed.json")
    if summary.get("status") != "passed" or completed.get("status") != "passed":
        raise RuntimeError("E阶段运行未通过，不能做最终验收")
    if summary.get("question_count") != 72 or summary.get("policy_count") != 16:
        raise RuntimeError("E阶段题数或策略数异常")
    if completed.get("runner_sha256") != runner._sha256_file(Path(__file__).resolve()):
        raise RuntimeError("E阶段运行后入口代码发生变化，必须使用新run重跑")
    selection = summary["final_selection"]
    selected = next(
        item for item in summary["policy_evaluations"]
        if item["name"] == selection["selected_policy"]
    )
    if not selected["strict_eligible"]:
        raise RuntimeError("E阶段选择了不满足严格门槛的策略")
    if selected["counts"]["false_rejected_answerable"] != 0:
        raise RuntimeError("E阶段所选策略误拒可回答题")
    if selected["counts"]["blocked_unanswerable"] < MIN_UNANSWERABLE_BLOCKED:
        raise RuntimeError("E阶段所选策略未达到不可回答拦截门槛")
    if selected["robustness"]["unstable_question_count"] != 0:
        raise RuntimeError("E阶段所选策略扰动不稳定")
    if any(
        binding["answerable_regressed_ids"] or binding["answerable_mixed_ids"]
        for binding in summary["context_bindings"]
    ):
        raise RuntimeError("E阶段上下文绑定存在可回答回归")
    per_question = _load_jsonl(phase_root / "per-question.jsonl")
    if len(per_question) != 72 or len({row["id"] for row in per_question}) != 72:
        raise RuntimeError("E阶段逐题产物不是72个唯一ID")
    if any(row["split"] != "development" for row in per_question):
        raise RuntimeError("E阶段逐题产物混入非development题")

    junit_path = junit_path.resolve()
    counts = _junit_counts(junit_path)
    if counts["tests"] != expected_tests:
        raise RuntimeError(
            f"E阶段JUnit测试数异常：{counts['tests']} != {expected_tests}"
        )
    if counts["failures"] or counts["errors"]:
        raise RuntimeError("E阶段JUnit存在失败或错误")
    d_root = experiment / "phase-d" / summary["source_phase_d_run_id"]
    input_now = {
        name: _artifact_identity(path) for name, path in _input_paths(d_root).items()
    }
    if input_now != summary["input_artifacts_after"]:
        raise RuntimeError("最终验收时D输入或题库hash已变化")
    storage_now = runner._validate_experiment_storage(experiment, protocol)
    if storage_now != summary["storage_after"]:
        raise RuntimeError("最终验收时隔离或生产存储发生变化")
    runner._source_artifact_checks(
        runner._storage_manifest(
            runner.SOURCE_RUN / "chroma", runner.SOURCE_RUN / "metadata.db"
        )
    )
    production = phase_b2._production_snapshot_check()
    if production["files_changed"]:
        raise RuntimeError("最终验收时生产快照发生变化")

    artifacts = {
        "protocol-completed.json": phase_root / "protocol-completed.json",
        "policies.json": phase_root / "policies.json",
        "per-question.jsonl": phase_root / "per-question.jsonl",
        "phase-e-summary.json": phase_root / "phase-e-summary.json",
        "phase-e-report.md": phase_root / "phase-e-report.md",
        "phase-e-conclusion.md": phase_root / "phase-e-conclusion.md",
        "../../../../run_phase_e_evidence_gate.py": Path(__file__).resolve(),
        "../../../../test_phase_e_evidence_gate.py": ROOT / "test_phase_e_evidence_gate.py",
        "junit": junit_path,
    }
    for name, path in artifacts.items():
        if not path.is_file():
            raise RuntimeError(f"最终验收缺少产物：{name} -> {path}")
    verification = {
        "schema_version": 1,
        "phase": "V3-5-E",
        "status": "passed",
        "verified_at": runner._now(),
        "run_id": run_id,
        "checks": {
            "question_count": 72,
            "answerable_count": 60,
            "unanswerable_count": 12,
            "policy_count": 16,
            "single_score_policy_count": 7,
            "multi_signal_policy_count": 8,
            "selected_policy": selection["selected_policy"],
            "selected_parameters": selection["selected_parameters"],
            "answerable_false_rejects": selected["counts"]["false_rejected_answerable"],
            "unanswerable_blocked": selected["counts"]["blocked_unanswerable"],
            "unanswerable_missed": selected["counts"]["missed_unanswerable"],
            "selected_policy_perturbation_unstable": selected["robustness"]["unstable_question_count"],
            "m8_perturbation_unstable": next(
                item["robustness"]["unstable_question_count"]
                for item in summary["policy_evaluations"] if item["name"] == "M8"
            ),
            "context_bindings": [
                {
                    "configuration": binding["configuration"],
                    "unanswerable_nonempty_before": binding["unanswerable_nonempty_before"],
                    "unanswerable_nonempty_after": binding["unanswerable_nonempty_after"],
                    "answerable_regressions": len(binding["answerable_regressed_ids"]),
                    "answerable_mixed": len(binding["answerable_mixed_ids"]),
                }
                for binding in summary["context_bindings"]
            ],
            "real_retrieval_queries": summary["real_retrieval_query_count"],
            "llm_calls": summary["llm_call_count"],
            "input_artifacts_unchanged": summary["input_artifacts_unchanged"],
            **_storage_facts(storage_now),
            "sealed_baseline_files_checked": summary["sealed_baseline_files_checked"],
            "production_snapshot_files_checked": production["files_checked"],
            "production_snapshot_files_changed": production["files_changed"],
            "pytest": counts,
        },
        "artifacts": {name: _artifact_identity(path) for name, path in artifacts.items()},
        "limits": summary["limits"],
    }
    runner._write_json_exclusive(
        phase_root / "phase-e-final-verification.json", verification
    )
    return verification


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="执行development离线门控重放")
    run_parser.add_argument("--experiment-id", default=DEFAULT_EXPERIMENT_ID)
    run_parser.add_argument("--phase-d-run-id", default=DEFAULT_PHASE_D_RUN_ID)
    run_parser.add_argument("--run-id", required=True)
    verify_parser = subparsers.add_parser("verify", help="结合JUnit执行只读最终验收")
    verify_parser.add_argument("--experiment-id", default=DEFAULT_EXPERIMENT_ID)
    verify_parser.add_argument("--run-id", required=True)
    verify_parser.add_argument("--junit", type=Path, required=True)
    verify_parser.add_argument("--expected-tests", type=int, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        result = run_phase_e(args.experiment_id, args.phase_d_run_id, args.run_id)
        summary = result["summary"]
        print(json.dumps({
            "status": summary["status"],
            "phase_root": result["phase_root"],
            "wall_s": summary["wall_s"],
            "final_selection": summary["final_selection"],
            "context_bindings": summary["context_bindings"],
        }, ensure_ascii=False, indent=2))
        return 0
    verification = verify_phase_e(
        args.experiment_id, args.run_id, args.junit, args.expected_tests
    )
    print(json.dumps(verification, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
