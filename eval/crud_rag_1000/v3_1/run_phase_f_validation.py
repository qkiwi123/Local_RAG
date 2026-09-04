"""V3-5 F：固定M2与两个上下文候选后的validation一次性验证。

本入口只读取预先划分的validation 24题（20道可回答、4道不可回答），在A阶段
复制的隔离索引上执行真实GPU检索。正式参数在运行前写入protocol且不根据结果
调整；Top10/配额2是唯一一次Dense20/Reranker20真实检索，Top5/配额2从同一
Reranker序列精确重放。M2只做离线门控，不调用生成模型、不实现业务gate、
不读取frozen_test、不修改生产默认值。G阶段三轮性能复测不在本入口执行。
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from eval.crud_rag_1000.v3_1 import run_phase_b_mode_ablation as phase_b
from eval.crud_rag_1000.v3_1 import run_phase_b2_hybrid_bm25_sweep as phase_b2
from eval.crud_rag_1000.v3_1 import run_phase_c_dense_sweep as phase_c
from eval.crud_rag_1000.v3_1 import run_phase_d_rerank_context_sweep as phase_d
from eval.crud_rag_1000.v3_1 import run_phase_e_evidence_gate as phase_e
from eval.crud_rag_1000.v3_1 import tuning_runner as runner


DEFAULT_EXPERIMENT_ID = "v35-a-baseline-20260829"
DEFAULT_PHASE_E_RUN_ID = "v35-e-evidence-gate-guard-20260831"
SOURCE_PHASE_D_RUN_ID = "v35-d-rerank-context-quality-20260830"

VALIDATION_ANSWERABLE = 20
VALIDATION_UNANSWERABLE = 4
MIN_UNANSWERABLE_BLOCKED = 3
MAX_ANSWERABLE_FALSE_REJECTS = 0
MIN_ALL_GOLD_COUNT = 19

M2_PARAMETERS = {
    "low_score": -2.5,
    "high_score": 0.5,
    "dense_best_weak": 0.52,
    "top1_dense_weak": 0.55,
}
QUALITY_CONTEXTS = (
    {"name": "top10-doccap2", "final_top_k": 10, "doc_cap": 2},
    {"name": "top5-doccap2", "final_top_k": 5, "doc_cap": 2},
)
HARD_QUALITY_METRICS = phase_c.HARD_QUALITY_METRICS


def _artifact_identity(path: Path) -> dict[str, Any]:
    return {"sha256": runner._sha256_file(path), "bytes": path.stat().st_size}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"JSONL解析失败：{path}:{line_number}: {exc}"
                ) from exc
    return rows


def _candidate_config() -> dict[str, Any]:
    payload = deepcopy(runner.BASELINE_CONFIG)
    payload.update({
        "mode": "dense",
        "dense_candidate_k": 20,
        "dense_distance_threshold": 0.67,
        "reranker_enabled": True,
        "reranker_candidate_k": 20,
        "final_top_k": 10,
        "max_chunks_per_document": 2,
    })
    return runner._validated_config_payload(payload)


def _original_baseline_config() -> dict[str, Any]:
    return runner._validated_config_payload(deepcopy(runner.BASELINE_CONFIG))


def _m2_policy() -> dict[str, Any]:
    return phase_e._multi_policy("M2", **M2_PARAMETERS)


def _validate_e_contract(experiment: Path, phase_e_run_id: str) -> dict[str, Any]:
    e_root = runner._assert_within(experiment / "phase-e" / phase_e_run_id, experiment)
    required = {
        "e_summary": e_root / "phase-e-summary.json",
        "e_completed": e_root / "protocol-completed.json",
        "e_verification": e_root / "phase-e-final-verification.json",
        "d_verification": (
            experiment / "phase-d" / SOURCE_PHASE_D_RUN_ID
            / "phase-d-final-verification.json"
        ),
    }
    for name, path in required.items():
        if not path.is_file():
            raise RuntimeError(f"F阶段缺少前置产物：{name} -> {path}")
    summary = runner._load_json(required["e_summary"])
    completed = runner._load_json(required["e_completed"])
    verification = runner._load_json(required["e_verification"])
    d_verification = runner._load_json(required["d_verification"])
    if any(
        payload.get("status") != "passed"
        for payload in (summary, completed, verification, d_verification)
    ):
        raise RuntimeError("D或E阶段最终验收未通过，不能进入F")
    selection = summary.get("final_selection", {})
    if selection.get("selected_policy") != "M2":
        raise RuntimeError("E阶段所选策略不是M2")
    if selection.get("selected_parameters") != M2_PARAMETERS:
        raise RuntimeError("E阶段M2参数与F冻结协议不一致")
    if summary.get("source_phase_d_run_id") != SOURCE_PHASE_D_RUN_ID:
        raise RuntimeError("E阶段来源D run与F冻结协议不一致")
    return {
        "root": e_root,
        "paths": required,
        "identities": {
            name: _artifact_identity(path) for name, path in required.items()
        },
        "selection": selection,
    }


def _all_gold_count(candidate: dict[str, Any]) -> int:
    return sum(
        bool(row.get("all_gold_docs_recalled"))
        for row in candidate.get("per_question", [])
        if row.get("answerable")
    )


def _hard_quality_checks(
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
    all_gold_count = _all_gold_count(candidate)
    return {
        "metrics_not_below_original_baseline": metric_checks,
        "no_answerable_regression_or_mixed_loss": no_question_loss,
        "all_gold_count": all_gold_count,
        "min_all_gold_count": MIN_ALL_GOLD_COUNT,
        "passed": (
            all(metric_checks.values())
            and no_question_loss
            and all_gold_count >= MIN_ALL_GOLD_COUNT
        ),
    }


def _gate_checks(evaluation: dict[str, Any]) -> dict[str, Any]:
    counts = evaluation["counts"]
    robustness = evaluation["robustness"]
    checks = {
        "answerable_false_rejects": counts["false_rejected_answerable"],
        "max_answerable_false_rejects": MAX_ANSWERABLE_FALSE_REJECTS,
        "unanswerable_blocked": counts["blocked_unanswerable"],
        "min_unanswerable_blocked": MIN_UNANSWERABLE_BLOCKED,
        "perturbation_unstable": robustness["unstable_question_count"],
        "require_all_perturbation_stable": True,
    }
    checks["passed"] = (
        checks["answerable_false_rejects"] <= MAX_ANSWERABLE_FALSE_REJECTS
        and checks["unanswerable_blocked"] >= MIN_UNANSWERABLE_BLOCKED
        and checks["perturbation_unstable"] == 0
    )
    return checks


def _quality_tuple(candidate: dict[str, Any]) -> tuple[float, ...]:
    return (
        float(candidate["all_gold_docs_recalled_rate"]),
        float(candidate["evidence_coverage"]),
        float(candidate["document_recall"]),
        float(candidate["multi_document_complete_recall_rate"]),
        float(candidate["document_hit_rate"]),
        float(candidate["document_mrr"]),
    )


def _select_candidate(contexts: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [row for row in contexts if row["eligible"]]
    if not eligible:
        return {
            "decision": "not_adopted",
            "selected_configuration": None,
            "eligible_configurations": [],
            "reason": "no_candidate_passed_frozen_validation_gates",
            "selection_rule": "no threshold or parameter retuning on validation",
        }
    by_name = {row["configuration"]: row for row in eligible}
    top10 = by_name.get("top10-doccap2")
    top5 = by_name.get("top5-doccap2")
    if top10 is not None and top5 is not None:
        top10_quality = _quality_tuple(top10["quality_before_gate_full"])
        top5_quality = _quality_tuple(top5["quality_before_gate_full"])
        top10_no_lower = all(left >= right for left, right in zip(top10_quality, top5_quality))
        top10_strictly_better = any(left > right for left, right in zip(top10_quality, top5_quality))
        winner = top10 if top10_no_lower and top10_strictly_better else top5
        reason = (
            "quality_first_top10_strictly_improves_without_metric_loss"
            if winner is top10
            else "quality_tie_or_mixed_result_prefers_smaller_context"
        )
    else:
        winner = max(eligible, key=lambda row: _quality_tuple(row["quality_before_gate_full"]))
        reason = "only_one_candidate_passed_frozen_validation_gates"
    return {
        "decision": "candidate_selected_for_g",
        "selected_configuration": winner["configuration"],
        "eligible_configurations": [row["configuration"] for row in eligible],
        "reason": reason,
        "selection_rule": (
            "quality-first: Top10 only wins when at least one ordered quality metric "
            "strictly improves and none decreases; otherwise prefer smaller context"
        ),
    }


def _compact_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    result = phase_b._compact_quality(candidate)
    result["all_gold_count"] = _all_gold_count(candidate)
    return result


def _context_result(
    questions: list[dict[str, Any]],
    baseline_candidate: dict[str, Any],
    baseline_rows: list[dict[str, Any]],
    replay_rows: list[dict[str, Any]],
    gate_evaluation: dict[str, Any],
    *,
    name: str,
    final_top_k: int,
    doc_cap: int,
) -> dict[str, Any]:
    before = phase_d._build_candidate(questions, replay_rows, candidate_k=final_top_k)
    gated_rows = phase_e._apply_gate(replay_rows, gate_evaluation["decisions"])
    after = phase_d._build_candidate(questions, gated_rows, candidate_k=final_top_k)
    baseline_comparison = phase_b._compare_to_control(
        baseline_candidate, before, baseline_rows, replay_rows
    )
    gate_comparison = phase_b._compare_to_control(before, after, replay_rows, gated_rows)
    quality_checks = _hard_quality_checks(baseline_candidate, before, baseline_comparison)
    gate_checks = _gate_checks(gate_evaluation)
    return {
        "configuration": name,
        "final_top_k": final_top_k,
        "max_chunks_per_document": doc_cap,
        "quality_before_gate": _compact_candidate(before),
        "quality_after_gate": _compact_candidate(after),
        "quality_before_gate_full": before,
        "quality_after_gate_full": after,
        "comparison_to_original_baseline": baseline_comparison,
        "gate_ablation_comparison": gate_comparison,
        "quality_checks": quality_checks,
        "gate_checks": gate_checks,
        "context_budget_before": phase_d._context_budget(replay_rows),
        "context_budget_after": phase_d._context_budget(gated_rows),
        "unanswerable_nonempty_before": sum(
            bool(row["retrieval"])
            for row, question in zip(replay_rows, questions)
            if not question["answerable"]
        ),
        "unanswerable_nonempty_after": sum(
            bool(row["retrieval"])
            for row, question in zip(gated_rows, questions)
            if not question["answerable"]
        ),
        "eligible": quality_checks["passed"] and gate_checks["passed"],
        "rows_before_gate": replay_rows,
        "rows_after_gate": gated_rows,
    }


def _public_context(context: dict[str, Any]) -> dict[str, Any]:
    omitted = {"quality_before_gate_full", "quality_after_gate_full", "rows_before_gate", "rows_after_gate"}
    return {key: value for key, value in context.items() if key not in omitted}


def _render_report(summary: dict[str, Any]) -> str:
    gate = summary["gate_validation"]
    lines = [
        "# V3-5 F validation一次性验证结果",
        "",
        f"- Run：`{summary['run_id']}`",
        "- 范围：validation 24题（20可回答+4不可回答）；不是未接触盲测",
        "- 固定门控：M2（low=-2.5、high=0.5、Dense弱阈值0.52/0.55）",
        "- 执行：隔离索引、真实GPU检索、无生成模型API；frozen_test未读取",
        "",
        "## M2留出验证",
        "",
        f"- 可回答误拒：{gate['counts']['false_rejected_answerable']}/20",
        f"- 不可回答拦截：{gate['counts']['blocked_unanswerable']}/4",
        f"- 扰动不稳定：{gate['robustness']['unstable_question_count']}/24",
        f"- 漏放不可回答：{'、'.join(gate['ids']['missed_unanswerable']) or '无'}",
        "",
        "## 上下文候选",
        "",
        "| 配置 | Gate | Hit | Recall | MRR | all-gold | Evidence | 不可回答非空 | tokens mean | F门槛 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for context in summary["contexts"]:
        for gate_label, quality_key, nonempty_key, budget_key in (
            ("off", "quality_before_gate", "unanswerable_nonempty_before", "context_budget_before"),
            ("M2", "quality_after_gate", "unanswerable_nonempty_after", "context_budget_after"),
        ):
            quality = context[quality_key]
            budget = context[budget_key]
            lines.append(
                f"| {context['configuration']} | {gate_label} | "
                f"{quality['document_hit_rate']:.4f} | {quality['document_recall']:.4f} | "
                f"{quality['document_mrr']:.4f} | {quality['all_gold_docs_recalled_rate']:.4f} | "
                f"{quality['evidence_coverage']:.4f} | {context[nonempty_key]}/4 | "
                f"{float(budget['ingest_token_count']['mean'] or 0):.2f} | "
                f"{'通过' if context['eligible'] else '不通过'} |"
            )
    baseline = summary["original_baseline"]
    lines += [
        "",
        "## 原始参数基线",
        "",
        f"- Hybrid15/Reranker15、Top5/配额3、门控关闭：Recall "
        f"{baseline['quality']['document_recall']:.4f}、all-gold "
        f"{baseline['quality']['all_gold_docs_recalled_rate']:.4f}（{baseline['quality']['all_gold_count']}/20）。",
        "",
        "## 选择与边界",
        "",
        f"- F决策：`{summary['final_selection']['decision']}`",
        f"- 拟进入G的唯一配置：`{summary['final_selection']['selected_configuration']}`",
        f"- 选择理由：`{summary['final_selection']['reason']}`",
        f"- Candidate首尾逐阶段稳定：{summary['candidate_control_stability']['passed']}",
        f"- Embedding/Reranker设备：{summary['runtime']['runtime']['embedding']['actual_device']} / "
        f"{summary['runtime']['runtime']['reranker']['actual_device']}",
        "- 本轮耗时仅作运行记录；G的交替3轮性能复测未执行。",
        "- M2仍是离线门控，不代表业务gate已实现；生产默认值未修改。",
        "",
    ]
    return "\n".join(lines)


def _render_conclusion(summary: dict[str, Any]) -> str:
    gate = summary["gate_validation"]
    selection = summary["final_selection"]
    return "\n".join([
        "# V3-5 F 结论",
        "",
        f"F已按冻结协议完成validation 24题一次性验证。M2误拒"
        f"{gate['counts']['false_rejected_answerable']}/20、拦截"
        f"{gate['counts']['blocked_unanswerable']}/4、扰动翻转"
        f"{gate['robustness']['unstable_question_count']}/24。",
        "",
        f"F决策为`{selection['decision']}`；唯一拟进入G的配置为"
        f"`{selection['selected_configuration']}`，理由为`{selection['reason']}`。",
        "",
        "本结论只覆盖检索与离线门控代理，不包含LLM回答/拒答质量。validation此前已被查看，"
        "不能包装成正式盲测；4道不可回答题也不足以估计总体拒答率。G三轮性能复测、"
        "S7业务接入、frozen_test和生产默认值均未执行。",
        "",
    ])


def run_phase_f(
    experiment_id: str,
    phase_e_run_id: str,
    run_id: str,
) -> dict[str, Any]:
    experiment, parent_protocol = runner._load_protocol(experiment_id)
    storage_before = runner._validate_experiment_storage(experiment, parent_protocol)
    run_id = runner._safe_name(run_id, "run_id")
    phase_e_run_id = runner._safe_name(phase_e_run_id, "phase_e_run_id")
    e_contract = _validate_e_contract(experiment, phase_e_run_id)
    phase_root = runner._assert_within(experiment / "phase-f" / run_id, experiment)
    phase_root.mkdir(parents=True, exist_ok=False)
    (phase_root / "configs").mkdir()
    (phase_root / "passes").mkdir()
    (phase_root / "contexts").mkdir()

    dataset = runner._dataset()
    if runner._sha256_file(runner.DATASET_PATH) != parent_protocol["dataset"]["sha256"]:
        raise RuntimeError("题库hash与A协议不一致")
    questions = runner._split_questions(dataset, "validation")
    if [question["id"] for question in questions] != parent_protocol["splits"]["validation"]:
        raise RuntimeError("validation题目ID与A协议不一致")
    if sum(bool(question["answerable"]) for question in questions) != VALIDATION_ANSWERABLE:
        raise RuntimeError("F阶段validation可回答题不是20道")
    if sum(not bool(question["answerable"]) for question in questions) != VALIDATION_UNANSWERABLE:
        raise RuntimeError("F阶段validation不可回答题不是4道")

    candidate_config = _candidate_config()
    baseline_config = _original_baseline_config()
    phase_protocol = {
        "schema_version": 1,
        "phase": "V3-5-F",
        "status": "running",
        "run_id": run_id,
        "created_at": runner._now(),
        "parent_experiment_id": experiment_id,
        "source_phase_e_run_id": phase_e_run_id,
        "dataset_sha256": parent_protocol["dataset"]["sha256"],
        "split": "validation",
        "question_ids": [question["id"] for question in questions],
        "question_count": len(questions),
        "answerable_count": VALIDATION_ANSWERABLE,
        "unanswerable_count": VALIDATION_UNANSWERABLE,
        "m2_parameters": M2_PARAMETERS,
        "formal_candidates": list(QUALITY_CONTEXTS),
        "original_baseline": {
            "mode": "hybrid",
            "dense_candidate_k": 15,
            "reranker_candidate_k": 15,
            "final_top_k": 5,
            "max_chunks_per_document": 3,
            "gate": "off",
        },
        "pass_schedule": [
            {"pass": "01-candidate-pre", "configuration": "dense20-reranker20-top10-doccap2"},
            {"pass": "02-original-baseline", "configuration": "hybrid15-reranker15-top5-doccap3"},
            {"pass": "03-candidate-post", "configuration": "dense20-reranker20-top10-doccap2"},
        ],
        "hard_acceptance": {
            "max_answerable_false_rejects": MAX_ANSWERABLE_FALSE_REJECTS,
            "min_unanswerable_blocked": MIN_UNANSWERABLE_BLOCKED,
            "all_decisions_perturbation_stable": True,
            "min_all_gold_count": MIN_ALL_GOLD_COUNT,
            "hard_quality_metrics_not_below_original_baseline": list(HARD_QUALITY_METRICS),
            "no_answerable_regression_or_mixed_loss": True,
        },
        "selection_rule": (
            "quality-first: Top10 wins only with strict quality improvement and no metric loss; "
            "otherwise Top5 wins; validation never retunes M2"
        ),
        "input_artifacts": e_contract["identities"],
        "runner_sha256": runner._sha256_file(Path(__file__).resolve()),
        "retrieval_code_identity": runner._retrieval_code_identity(),
        "environment": parent_protocol["environment"],
        "network_policy": parent_protocol["network_policy"],
        "llm_policy": parent_protocol["llm_policy"],
        "limits": [
            "validation 24 only; split was previously viewed and is not a clean blind test",
            "fixed M2 and at most two formal context candidates plus original baseline",
            "no threshold or parameter retuning after validation results",
            "frozen_test not read",
            "real retrieval on copied experiment database only",
            "no LLM/API generation call",
            "offline gate only; no S7 business implementation",
            "no production setting change",
            "G three-round performance validation not executed",
        ],
    }
    runner._write_json_exclusive(phase_root / "protocol.json", phase_protocol)
    config_records = {
        "candidate": {
            "name": "dense20-reranker20-top10-doccap2",
            "config_id": runner._config_id(candidate_config),
            "retrieval": candidate_config,
            "effective_config": runner._effective_config(candidate_config),
            "gate": {"name": "M2", "parameters": M2_PARAMETERS, "implemented_offline": True},
        },
        "original_baseline": {
            "name": "hybrid15-reranker15-top5-doccap3",
            "config_id": runner._config_id(baseline_config),
            "retrieval": baseline_config,
            "effective_config": runner._effective_config(baseline_config),
            "gate": {"name": "gate-off", "implemented_offline": True},
        },
    }
    for name, record in config_records.items():
        runner._write_json_exclusive(phase_root / "configs" / f"{name}.json", record)

    storage = experiment / "storage"
    os.environ.update({
        **parent_protocol["environment"],
        "CHROMA_PERSIST_DIR": str(storage / "chroma"),
        "METADATA_DB_PATH": str(storage / "metadata.db"),
        "UPLOAD_DIR": str(storage / "uploads"),
    })
    blocked: list[dict[str, Any]] = []
    started = time.perf_counter()
    runner._install_network_guard(blocked, started)
    from eval import adapters
    from backend.services.vector_store import get_all_chunks
    from eval.retrieval_sweep import build_candidate_result

    adapters.assert_isolation()
    warmup = adapters.retrieve_only(
        questions[0]["question"],
        include_trace=True,
        runtime_config=runner._runtime_config(candidate_config),
    )
    if warmup["trace"]["effective_config"] != runner._effective_config(candidate_config):
        raise RuntimeError("F预热候选参数未实际生效")
    runner._write_json_exclusive(phase_root / "warmup.json", {
        "id": questions[0]["id"],
        "retrieval_s": warmup["retrieval_s"],
        "effective_config": warmup["trace"]["effective_config"],
        "not_in_quality_or_formal_query_count": True,
    })
    all_chunks = {row["chunk_id"]: row for row in get_all_chunks()}
    if len(all_chunks) != 2914:
        raise RuntimeError(f"隔离Chroma块数异常：{len(all_chunks)}")

    pass_rows: dict[str, list[dict[str, Any]]] = {}
    pass_candidates: dict[str, dict[str, Any]] = {}
    pass_configs = (
        ("01-candidate-pre", "candidate", candidate_config),
        ("02-original-baseline", "original_baseline", baseline_config),
        ("03-candidate-post", "candidate", candidate_config),
    )
    for pass_index, (pass_name, config_name, config) in enumerate(pass_configs, 1):
        print(f"[F {pass_index}/{len(pass_configs)}] {pass_name}", flush=True)
        rows: list[dict[str, Any]] = []
        rows_path = phase_root / "passes" / f"{pass_name}.jsonl"
        runtime_config = runner._runtime_config(config)
        with rows_path.open("x", encoding="utf-8") as handle:
            for index, question in enumerate(questions, 1):
                result = adapters.retrieve_only(
                    question["question"], include_trace=True, runtime_config=runtime_config
                )
                trace = result.get("trace")
                if trace is None or trace.get("effective_config") != runner._effective_config(config):
                    raise RuntimeError(f"{pass_name}参数未生效或Trace缺失：{question['id']}")
                row = {
                    "id": question["id"],
                    "split": "validation",
                    "pass": pass_name,
                    "configuration": config_records[config_name]["name"],
                    "config_id": config_records[config_name]["config_id"],
                    "retrieval": result["retrieval"],
                    "retrieval_s": result["retrieval_s"],
                    "trace": trace,
                }
                phase_c._validate_trace(row, config)
                if config_name == "candidate":
                    phase_d._validate_trace(row, config, all_chunks)
                rows.append(row)
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                if index % 8 == 0:
                    print(f"  {index}/{len(questions)}", flush=True)
        candidate = build_candidate_result(
            candidate_k=config["final_top_k"],
            questions=questions,
            retrievals={row["id"]: row["retrieval"] for row in rows},
            timings_s=[row["retrieval_s"] for row in rows],
            traces={row["id"]: row["trace"] for row in rows},
        )
        pass_rows[pass_name] = rows
        pass_candidates[pass_name] = candidate
        runner._write_json_exclusive(phase_root / "passes" / f"{pass_name}-summary.json", {
            "pass": pass_name,
            "configuration": config_records[config_name]["name"],
            "question_count": len(rows),
            "quality": _compact_candidate(candidate),
            "latency": runner._latency(rows),
            "candidate_counts": phase_c._candidate_counts(rows),
        })

    candidate_stability = phase_c._control_stability(
        pass_rows["01-candidate-pre"],
        pass_rows["03-candidate-post"],
        ("dense_raw", "dense_eligible", "reranker", "selected"),
    )
    if not candidate_stability["passed"]:
        raise RuntimeError("F候选首尾control不稳定，validation结果不可归因")

    candidate_source_rows = pass_rows["01-candidate-pre"]
    baseline_rows = pass_rows["02-original-baseline"]
    baseline_candidate = pass_candidates["02-original-baseline"]
    features_by_id = {
        row["id"]: phase_e._extract_features(row) for row in candidate_source_rows
    }
    gate_evaluation = phase_e._evaluate_policy(_m2_policy(), questions, features_by_id)

    contexts_full: list[dict[str, Any]] = []
    for spec in QUALITY_CONTEXTS:
        replay_rows = phase_d._replay_rows(
            candidate_source_rows,
            all_chunks,
            top_k=int(spec["final_top_k"]),
            doc_cap=int(spec["doc_cap"]),
        )
        if spec["name"] == "top10-doccap2":
            if [row["retrieval"] for row in replay_rows] != [
                row["retrieval"] for row in candidate_source_rows
            ]:
                raise RuntimeError("F Top10重放与真实业务selected不一致")
        context = _context_result(
            questions,
            baseline_candidate,
            baseline_rows,
            replay_rows,
            gate_evaluation,
            name=str(spec["name"]),
            final_top_k=int(spec["final_top_k"]),
            doc_cap=int(spec["doc_cap"]),
        )
        contexts_full.append(context)
        runner._write_json_exclusive(
            phase_root / "contexts" / f"{spec['name']}.json",
            _public_context(context),
        )
    if len({row["unanswerable_nonempty_after"] for row in contexts_full}) != 1:
        raise RuntimeError("Top10/Top5共享M2信号却产生不同门控结果")

    final_selection = _select_candidate(contexts_full)
    question_by_id = {question["id"]: question for question in questions}
    rows_by_context = {
        context["configuration"]: {
            before["id"]: (before, after)
            for before, after in zip(
                context["rows_before_gate"],
                context["rows_after_gate"],
            )
        }
        for context in contexts_full
    }
    with (phase_root / "per-question.jsonl").open("x", encoding="utf-8") as handle:
        for source_row in candidate_source_rows:
            question_id = source_row["id"]
            decision = gate_evaluation["decisions"][question_id]
            contexts = {}
            for spec in QUALITY_CONTEXTS:
                before, after = rows_by_context[str(spec["name"])][question_id]
                contexts[str(spec["name"])] = {
                    "before_chunk_ids": [item["chunk_id"] for item in before["retrieval"]],
                    "after_chunk_ids": [item["chunk_id"] for item in after["retrieval"]],
                }
            payload = {
                "id": question_id,
                "split": "validation",
                "answerable": question_by_id[question_id]["answerable"],
                "category": question_by_id[question_id]["category"],
                "features": features_by_id[question_id],
                "m2_decision": decision,
                "contexts": contexts,
            }
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    runtime = adapters.get_run_config()
    for component in ("embedding", "reranker"):
        status = runtime["runtime"][component]
        if not (
            status["loaded"]
            and status["verified"]
            and status["actual_device"] == "cuda:0"
        ):
            raise RuntimeError(f"F阶段{component}未在cuda:0真实运行")
    if "backend.services.llm" in sys.modules:
        raise RuntimeError("F阶段意外导入LLM模块")
    if blocked:
        raise RuntimeError("F阶段出现出站网络尝试")

    input_after = {
        name: _artifact_identity(path) for name, path in e_contract["paths"].items()
    }
    if input_after != e_contract["identities"]:
        raise RuntimeError("F运行期间D/E固定输入发生变化")
    storage_after = runner._validate_experiment_storage(experiment, parent_protocol)
    if storage_before != storage_after:
        raise RuntimeError("F运行期间隔离或生产存储发生变化")
    runner._source_artifact_checks(
        runner._storage_manifest(
            runner.SOURCE_RUN / "chroma", runner.SOURCE_RUN / "metadata.db"
        )
    )
    production = phase_b2._production_snapshot_check()
    if production["files_changed"]:
        raise RuntimeError("F运行期间生产快照发生变化")

    contexts = [_public_context(context) for context in contexts_full]
    summary = {
        "schema_version": 1,
        "phase": "V3-5-F",
        "status": "passed",
        "run_id": run_id,
        "parent_experiment_id": experiment_id,
        "source_phase_e_run_id": phase_e_run_id,
        "completed_at": runner._now(),
        "wall_s": round(time.perf_counter() - started, 3),
        "split": "validation",
        "question_count": len(questions),
        "answerable_count": VALIDATION_ANSWERABLE,
        "unanswerable_count": VALIDATION_UNANSWERABLE,
        "formal_candidate_count": len(QUALITY_CONTEXTS),
        "formal_real_retrieval_query_count": len(questions) * len(pass_configs),
        "warmup_query_count": 1,
        "llm_call_count": 0,
        "frozen_test_question_count_read": 0,
        "m2_parameters": M2_PARAMETERS,
        "gate_validation": {
            key: value for key, value in gate_evaluation.items() if key != "decisions"
        },
        "original_baseline": {
            "configuration": "hybrid15-reranker15-top5-doccap3",
            "quality": _compact_candidate(baseline_candidate),
            "latency": runner._latency(baseline_rows),
            "context_budget": phase_d._context_budget(baseline_rows),
        },
        "contexts": contexts,
        "final_selection": final_selection,
        "candidate_control_stability": candidate_stability,
        "pass_latencies": {
            name: runner._latency(rows) for name, rows in pass_rows.items()
        },
        "runtime": runtime,
        "input_artifacts_before": e_contract["identities"],
        "input_artifacts_after": input_after,
        "input_artifacts_unchanged": input_after == e_contract["identities"],
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
    runner._write_json_exclusive(phase_root / "phase-f-summary.json", summary)
    (phase_root / "phase-f-report.md").write_text(_render_report(summary), encoding="utf-8")
    (phase_root / "phase-f-conclusion.md").write_text(
        _render_conclusion(summary), encoding="utf-8"
    )
    runner._write_json_exclusive(phase_root / "protocol-completed.json", {
        **phase_protocol,
        "status": "passed",
        "completed_at": summary["completed_at"],
        "gate_validation": summary["gate_validation"],
        "final_selection": final_selection,
    })
    return {"phase_root": str(phase_root), "summary": summary}


def verify_phase_f(
    experiment_id: str,
    run_id: str,
    junit_path: Path,
    expected_tests: int,
) -> dict[str, Any]:
    experiment, parent_protocol = runner._load_protocol(experiment_id)
    run_id = runner._safe_name(run_id, "run_id")
    phase_root = runner._assert_within(experiment / "phase-f" / run_id, experiment)
    summary = runner._load_json(phase_root / "phase-f-summary.json")
    completed = runner._load_json(phase_root / "protocol-completed.json")
    if summary.get("status") != "passed" or completed.get("status") != "passed":
        raise RuntimeError("F阶段运行未通过，不能做最终验收")
    if summary.get("split") != "validation" or summary.get("question_count") != 24:
        raise RuntimeError("F阶段split或题数异常")
    if summary.get("answerable_count") != 20 or summary.get("unanswerable_count") != 4:
        raise RuntimeError("F阶段可回答/不可回答题数异常")
    if completed.get("runner_sha256") != runner._sha256_file(Path(__file__).resolve()):
        raise RuntimeError("F阶段运行后入口代码发生变化，必须使用新run重跑")
    if summary.get("m2_parameters") != M2_PARAMETERS:
        raise RuntimeError("F阶段M2参数不是冻结值")
    gate = summary["gate_validation"]
    gate_checks = _gate_checks(gate)
    if gate_checks["passed"] != all(context["gate_checks"]["passed"] for context in summary["contexts"]):
        raise RuntimeError("F阶段门控资格汇总不一致")
    selection = summary["final_selection"]
    eligible_names = [
        context["configuration"] for context in summary["contexts"] if context["eligible"]
    ]
    if selection["decision"] == "candidate_selected_for_g":
        if selection["selected_configuration"] not in eligible_names:
            raise RuntimeError("F选择了不合格候选")
    elif selection["decision"] != "not_adopted" or eligible_names:
        raise RuntimeError("F未采用结论与候选资格不一致")
    if not summary["candidate_control_stability"]["passed"]:
        raise RuntimeError("F候选control不稳定")
    runtime = summary["runtime"]["runtime"]
    if any(runtime[name]["actual_device"] != "cuda:0" for name in ("embedding", "reranker")):
        raise RuntimeError("F最终产物未证明两模型在cuda:0")
    if summary.get("formal_real_retrieval_query_count") != 72:
        raise RuntimeError("F正式真实检索题次不是72")
    if summary.get("llm_call_count") != 0 or summary.get("frozen_test_question_count_read") != 0:
        raise RuntimeError("F越界调用LLM或读取frozen_test")

    per_question = _load_jsonl(phase_root / "per-question.jsonl")
    if len(per_question) != 24 or len({row["id"] for row in per_question}) != 24:
        raise RuntimeError("F逐题产物不是24个唯一ID")
    if any(row.get("split") != "validation" for row in per_question):
        raise RuntimeError("F逐题产物混入非validation题")

    junit_path = junit_path.resolve()
    counts = phase_e._junit_counts(junit_path)
    if counts["tests"] != expected_tests:
        raise RuntimeError(f"F阶段JUnit测试数异常：{counts['tests']} != {expected_tests}")
    if counts["failures"] or counts["errors"]:
        raise RuntimeError("F阶段JUnit存在失败或错误")
    e_contract = _validate_e_contract(
        experiment, str(summary["source_phase_e_run_id"])
    )
    if e_contract["identities"] != summary["input_artifacts_after"]:
        raise RuntimeError("最终验收时D/E固定输入发生变化")
    storage_now = runner._validate_experiment_storage(experiment, parent_protocol)
    if storage_now != summary["storage_after"]:
        raise RuntimeError("最终验收时隔离或生产存储发生变化")
    runner._source_artifact_checks(
        runner._storage_manifest(
            runner.SOURCE_RUN / "chroma", runner.SOURCE_RUN / "metadata.db"
        )
    )
    production = phase_b2._production_snapshot_check()
    if production["files_changed"]:
        raise RuntimeError("F最终验收时生产快照发生变化")
    storage_facts = phase_e._storage_facts(storage_now)

    artifacts = {
        "protocol-completed.json": phase_root / "protocol-completed.json",
        "per-question.jsonl": phase_root / "per-question.jsonl",
        "phase-f-summary.json": phase_root / "phase-f-summary.json",
        "phase-f-report.md": phase_root / "phase-f-report.md",
        "phase-f-conclusion.md": phase_root / "phase-f-conclusion.md",
        "../../../../run_phase_f_validation.py": Path(__file__).resolve(),
        "../../../../test_phase_f_validation.py": ROOT / "test_phase_f_validation.py",
        "junit": junit_path,
    }
    for name, path in artifacts.items():
        if not path.is_file():
            raise RuntimeError(f"F最终验收缺少产物：{name} -> {path}")
    verification = {
        "schema_version": 1,
        "phase": "V3-5-F",
        "status": "passed",
        "verified_at": runner._now(),
        "run_id": run_id,
        "checks": {
            "split": "validation",
            "question_count": 24,
            "answerable_count": 20,
            "unanswerable_count": 4,
            "m2_parameters": M2_PARAMETERS,
            "answerable_false_rejects": gate["counts"]["false_rejected_answerable"],
            "unanswerable_blocked": gate["counts"]["blocked_unanswerable"],
            "unanswerable_missed": gate["counts"]["missed_unanswerable"],
            "perturbation_unstable": gate["robustness"]["unstable_question_count"],
            "gate_passed": gate_checks["passed"],
            "candidate_control_stable": True,
            "eligible_configurations": eligible_names,
            "final_selection": selection,
            "formal_real_retrieval_queries": 72,
            "warmup_queries": summary["warmup_query_count"],
            "llm_calls": 0,
            "frozen_test_questions_read": 0,
            "embedding_device": runtime["embedding"]["actual_device"],
            "reranker_device": runtime["reranker"]["actual_device"],
            "input_artifacts_unchanged": summary["input_artifacts_unchanged"],
            **storage_facts,
            "sealed_baseline_files_checked": summary["sealed_baseline_files_checked"],
            "production_snapshot_files_checked": production["files_checked"],
            "production_snapshot_files_changed": production["files_changed"],
            "pytest": counts,
        },
        "artifacts": {name: _artifact_identity(path) for name, path in artifacts.items()},
        "limits": summary["limits"],
    }
    runner._write_json_exclusive(
        phase_root / "phase-f-final-verification.json", verification
    )
    return verification


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="执行固定M2的validation真实检索验证")
    run_parser.add_argument("--experiment-id", default=DEFAULT_EXPERIMENT_ID)
    run_parser.add_argument("--phase-e-run-id", default=DEFAULT_PHASE_E_RUN_ID)
    run_parser.add_argument("--run-id", required=True)
    verify_parser = subparsers.add_parser("verify", help="结合JUnit执行F最终只读验收")
    verify_parser.add_argument("--experiment-id", default=DEFAULT_EXPERIMENT_ID)
    verify_parser.add_argument("--run-id", required=True)
    verify_parser.add_argument("--junit", type=Path, required=True)
    verify_parser.add_argument("--expected-tests", type=int, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        result = run_phase_f(args.experiment_id, args.phase_e_run_id, args.run_id)
        print(json.dumps({
            "phase_root": result["phase_root"],
            "status": result["summary"]["status"],
            "final_selection": result["summary"]["final_selection"],
        }, ensure_ascii=False, indent=2))
        return 0
    result = verify_phase_f(
        args.experiment_id, args.run_id, args.junit, args.expected_tests
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
