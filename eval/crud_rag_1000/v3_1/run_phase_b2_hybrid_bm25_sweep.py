"""V3-5 B2：Hybrid BM25/RRF受控坐标扫描。

本入口复用A阶段隔离索引，只执行development 72题。固定D阶段选出的
Dense K20/Reranker K20与Top5/单文档3块，依次扫描BM25 TopK、k1、b和
RRF常数；同一GPU进程首尾重复Dense与Hybrid control，不调用生成模型，
不修改生产默认值，也不自动进入E或重做D。
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from importlib.metadata import PackageNotFoundError, version
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
from eval.crud_rag_1000.v3_1 import run_phase_c_dense_sweep as phase_c
from eval.crud_rag_1000.v3_1 import run_phase_d_rerank_context_sweep as phase_d
from eval.crud_rag_1000.v3_1 import tuning_runner as runner


DENSE_K = 20
RERANKER_K = 20
DISTANCE = 0.67
FINAL_K = 5
DOC_CAP = 3
DEFAULT_BM25_K = 15
DEFAULT_K1 = 1.5
DEFAULT_B = 0.75
DEFAULT_RRF_K = 60

BM25_K_VALUES = (10, 15, 20, 30, 50)
K1_VALUES = (0.8, 1.2, 1.5, 2.0)
B_VALUES = (0.25, 0.5, 0.75, 1.0)
RRF_VALUES = (20, 40, 60, 80)
HARD_QUALITY_METRICS = phase_c.HARD_QUALITY_METRICS
RERANKER_FP16_SCORE_ATOL = 0.02
RERANKER_EXACT_TIE_ATOL = 1e-12


def _b2_code_identity() -> dict[str, str]:
    paths = (
        PROJECT_ROOT / "backend/services/retriever.py",
        PROJECT_ROOT / "backend/services/embedding.py",
        PROJECT_ROOT / "backend/services/vector_store.py",
        PROJECT_ROOT / "backend/services/sparse_retriever.py",
        PROJECT_ROOT / "backend/services/fusion.py",
        PROJECT_ROOT / "backend/services/reranker.py",
        PROJECT_ROOT / "backend/services/retrieval_trace.py",
        PROJECT_ROOT / "backend/services/torch_device.py",
        PROJECT_ROOT / "backend/models/settings_schemas.py",
        PROJECT_ROOT / "backend/models/schemas.py",
        PROJECT_ROOT / "eval/adapters.py",
        PROJECT_ROOT / "eval/retrieval_sweep.py",
        Path(__file__).resolve(),
    )
    return {
        path.relative_to(PROJECT_ROOT).as_posix(): runner._sha256_file(path)
        for path in paths
    }


def _dependency_versions() -> dict[str, str]:
    result: dict[str, str] = {}
    for package in ("rank-bm25", "jieba", "FlagEmbedding"):
        try:
            result[package] = version(package)
        except PackageNotFoundError:  # pragma: no cover - real run requires packages
            result[package] = "not-installed"
    return result


def _hybrid_config(
    *,
    bm25_k: int = DEFAULT_BM25_K,
    k1: float = DEFAULT_K1,
    b: float = DEFAULT_B,
    rrf_k: int = DEFAULT_RRF_K,
) -> dict[str, Any]:
    payload = deepcopy(runner.BASELINE_CONFIG)
    payload.update({
        "mode": "hybrid",
        "final_top_k": FINAL_K,
        "dense_candidate_k": DENSE_K,
        "dense_distance_threshold": DISTANCE,
        "bm25_top_k": bm25_k,
        "bm25_k1": k1,
        "bm25_b": b,
        "rrf_k": rrf_k,
        "reranker_enabled": True,
        "reranker_candidate_k": RERANKER_K,
        "max_chunks_per_document": DOC_CAP,
    })
    return runner._validated_config_payload(payload)


def _dense_control_config() -> dict[str, Any]:
    payload = _hybrid_config()
    payload["mode"] = "dense"
    return runner._validated_config_payload(payload)


def _value_token(value: float | int) -> str:
    if isinstance(value, int):
        return str(value)
    return str(int(round(float(value) * 100))).zfill(3)


def _config_name(config: dict[str, Any]) -> str:
    return (
        f"hybrid-bm{config['bm25_top_k']}-k1{_value_token(config['bm25_k1'])}"
        f"-b{_value_token(config['bm25_b'])}-rrf{config['rrf_k']}"
    )


def _stage_candidates(row: dict[str, Any], stage: str) -> list[dict[str, Any]]:
    return phase_c._stage_candidates(row, stage)


def _candidate_counts(rows: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    result: dict[str, dict[str, float | int]] = {}
    for stage in ("dense_raw", "dense_eligible", "bm25", "rrf", "reranker", "selected"):
        values = [len(_stage_candidates(row, stage)) for row in rows]
        result[stage] = {
            "min": min(values),
            "max": max(values),
            "mean": round(sum(values) / len(values), 4),
        }
    return result


def _stage_signature(
    row: dict[str, Any], stage: str, fields: tuple[str, ...]
) -> list[tuple[Any, ...]]:
    return [
        tuple(candidate.get(field) for field in fields)
        for candidate in _stage_candidates(row, stage)
    ]


def _cross_config_invariants(
    *,
    dense_control_rows: list[dict[str, Any]],
    evaluations: list[dict[str, Any]],
    pass_rows: dict[str, list[dict[str, Any]]],
    topk_evaluations: list[dict[str, Any]],
    rrf_evaluations: list[dict[str, Any]],
) -> dict[str, Any]:
    dense_fields = (
        "chunk_id", "rank", "dense_rank", "distance",
        "eligible", "filtered_reason",
    )
    bm25_fields = ("chunk_id", "rank", "bm25_rank", "bm25_score", "distance")
    dense_exact: dict[str, str] = {}
    for evaluation in evaluations:
        rows = pass_rows[evaluation["pass"]]
        differences = [
            left["id"]
            for left, right in zip(dense_control_rows, rows)
            if (
                _stage_signature(left, "dense_raw", dense_fields)
                != _stage_signature(right, "dense_raw", dense_fields)
                or _stage_signature(left, "dense_eligible", dense_fields)
                != _stage_signature(right, "dense_eligible", dense_fields)
            )
        ]
        if differences:
            raise RuntimeError(
                f"{evaluation['configuration']} Dense分支与同期control不同：{differences[:5]}"
            )
        dense_exact[evaluation["configuration"]] = f"{len(rows)}/{len(rows)}"

    unique_topk = {
        row["config_id"]: row for row in topk_evaluations
    }
    ordered_topk = sorted(unique_topk.values(), key=lambda row: row["bm25_k"])
    prefix_pairs = 0
    for small_index, small in enumerate(ordered_topk):
        for large in ordered_topk[small_index + 1:]:
            for small_row, large_row in zip(
                pass_rows[small["pass"]], pass_rows[large["pass"]]
            ):
                small_stage = _stage_signature(small_row, "bm25", bm25_fields)
                large_stage = _stage_signature(large_row, "bm25", bm25_fields)
                if small_stage != large_stage[:len(small_stage)]:
                    raise RuntimeError(
                        f"BM25 TopK扩展不保持前缀：{small_row['id']} "
                        f"K{small['bm25_k']}→K{large['bm25_k']}"
                    )
                if len(small_stage) < int(small["bm25_k"]) and len(large_stage) != len(small_stage):
                    raise RuntimeError(f"BM25正分候选耗尽后仍随TopK增加：{small_row['id']}")
            prefix_pairs += 1

    unique_rrf = {row["config_id"]: row for row in rrf_evaluations}
    rrf_rows = list(unique_rrf.values())
    rrf_upstream_pairs = 0
    if rrf_rows:
        reference = rrf_rows[0]
        for candidate in rrf_rows[1:]:
            for left, right in zip(
                pass_rows[reference["pass"]], pass_rows[candidate["pass"]]
            ):
                for stage, fields in (
                    ("dense_raw", dense_fields),
                    ("dense_eligible", dense_fields),
                    ("bm25", bm25_fields),
                ):
                    if _stage_signature(left, stage, fields) != _stage_signature(right, stage, fields):
                        raise RuntimeError(
                            f"仅改变RRF_K却改变上游{stage}：{left['id']}"
                        )
            rrf_upstream_pairs += 1

    shared_score_ranges: dict[tuple[str, str], list[float | int]] = {}
    same_set_references: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    same_set_comparisons = 0
    same_set_order_differences = 0
    same_set_tie_inversions = 0
    for evaluation in evaluations:
        for row in pass_rows[evaluation["pass"]]:
            reranker = _stage_candidates(row, "reranker")
            score_by_chunk = {
                candidate["chunk_id"]: float(candidate["reranker_score"])
                for candidate in reranker
            }
            reranker_ids = [candidate["chunk_id"] for candidate in reranker]
            selected_ids = [
                candidate["chunk_id"] for candidate in _stage_candidates(row, "selected")
            ]
            candidate_set_key = (row["id"], tuple(sorted(reranker_ids)))
            if candidate_set_key in same_set_references:
                same_set_comparisons += 1
                reference = same_set_references[candidate_set_key]
                if selected_ids != reference["selected_ids"]:
                    raise RuntimeError(
                        f"相同Reranker候选集的最终Top{FINAL_K}变化：{row['id']}"
                    )
                if reranker_ids != reference["reranker_ids"]:
                    same_set_order_differences += 1
                    reference_positions = {
                        chunk_id: index
                        for index, chunk_id in enumerate(reference["reranker_ids"])
                    }
                    current_positions = {
                        chunk_id: index for index, chunk_id in enumerate(reranker_ids)
                    }
                    for left_index, left_id in enumerate(reranker_ids):
                        for right_id in reranker_ids[left_index + 1:]:
                            if reference_positions[left_id] < reference_positions[right_id]:
                                continue
                            if current_positions[left_id] >= current_positions[right_id]:
                                continue
                            if (
                                abs(reference["scores"][left_id] - reference["scores"][right_id])
                                > RERANKER_EXACT_TIE_ATOL
                                or abs(score_by_chunk[left_id] - score_by_chunk[right_id])
                                > RERANKER_EXACT_TIE_ATOL
                            ):
                                raise RuntimeError(
                                    f"相同Reranker候选集出现非同分排序反转：{row['id']} / "
                                    f"{left_id} / {right_id}"
                                )
                            same_set_tie_inversions += 1
            else:
                same_set_references[candidate_set_key] = {
                    "reranker_ids": reranker_ids,
                    "selected_ids": selected_ids,
                    "scores": score_by_chunk,
                }

            for candidate in reranker:
                key = (row["id"], candidate["chunk_id"])
                score = float(candidate["reranker_score"])
                if key in shared_score_ranges:
                    value_range = shared_score_ranges[key]
                    value_range[0] = min(float(value_range[0]), score)
                    value_range[1] = max(float(value_range[1]), score)
                    value_range[2] = int(value_range[2]) + 1
                else:
                    shared_score_ranges[key] = [score, score, 1]

    shared_score_comparisons = sum(
        int(value_range[2]) - 1
        for value_range in shared_score_ranges.values()
        if int(value_range[2]) > 1
    )
    varying_shared_pairs = {
        key: float(value_range[1]) - float(value_range[0])
        for key, value_range in shared_score_ranges.items()
        if int(value_range[2]) > 1
        and float(value_range[1]) - float(value_range[0]) > 0
    }
    shared_score_max_delta = max(varying_shared_pairs.values(), default=0.0)
    if shared_score_max_delta > RERANKER_FP16_SCORE_ATOL:
        key = max(varying_shared_pairs, key=varying_shared_pairs.get)
        raise RuntimeError(
            f"共享query-chunk的Reranker分数跨配置变化超过FP16容差："
            f"{key[0]} / {key[1]} / delta={varying_shared_pairs[key]}"
        )

    return {
        "all_hybrid_dense_matches_dense_control": True,
        "dense_exact_by_configuration": dense_exact,
        "bm25_topk_prefix_pair_count": prefix_pairs,
        "bm25_topk_prefix_stable": True,
        "rrf_only_upstream_comparison_count": rrf_upstream_pairs,
        "rrf_only_upstream_stable": True,
        "reranker_shared_score_comparisons": shared_score_comparisons,
        "reranker_shared_varying_pair_count": len(varying_shared_pairs),
        "reranker_shared_varying_question_count": len({
            key[0] for key in varying_shared_pairs
        }),
        "reranker_shared_score_max_absolute_delta": shared_score_max_delta,
        "reranker_shared_scores_stable_with_tolerance": RERANKER_FP16_SCORE_ATOL,
        "same_reranker_candidate_set_comparison_count": same_set_comparisons,
        "same_reranker_candidate_set_selected_top5_exact": True,
        "same_reranker_candidate_set_order_difference_count": (
            same_set_order_differences
        ),
        "same_reranker_candidate_set_exact_tie_inversion_count": (
            same_set_tie_inversions
        ),
    }


def _validate_hybrid_trace(row: dict[str, Any], config: dict[str, Any]) -> None:
    from backend.services.fusion import reciprocal_rank_fusion

    phase_c._validate_trace(row, config)
    bm25 = _stage_candidates(row, "bm25")
    if len(bm25) > config["bm25_top_k"]:
        raise RuntimeError(f"{row['id']} BM25候选数超过bm25_top_k")
    if [item.get("bm25_rank") for item in bm25] != list(range(1, len(bm25) + 1)):
        raise RuntimeError(f"{row['id']} BM25 rank不连续")
    if any(
        item.get("bm25_score") is None
        or float(item["bm25_score"]) <= 0
        or item.get("distance") is not None
        for item in bm25
    ):
        raise RuntimeError(f"{row['id']} BM25候选缺少正分")
    if any(
        float(left["bm25_score"]) < float(right["bm25_score"])
        for left, right in zip(bm25, bm25[1:])
    ):
        raise RuntimeError(f"{row['id']} BM25候选未按分数降序")
    if any(
        abs(float(left["bm25_score"]) - float(right["bm25_score"])) <= 1e-15
        and left["chunk_id"] > right["chunk_id"]
        for left, right in zip(bm25, bm25[1:])
    ):
        raise RuntimeError(f"{row['id']} BM25同分候选未按chunk_id稳定排序")

    dense = _stage_candidates(row, "dense_eligible")
    fusion_k = runner._fusion_pool_k(config)
    expected = reciprocal_rank_fusion(
        dense,
        bm25,
        rrf_k=config["rrf_k"],
        top_k=fusion_k,
    )
    actual = _stage_candidates(row, "rrf")
    if [item["chunk_id"] for item in actual] != [item["chunk_id"] for item in expected]:
        raise RuntimeError(f"{row['id']} RRF顺序与真实双路排名重算不一致")
    for observed, wanted in zip(actual, expected):
        if abs(float(observed["rrf_score"]) - float(wanted["rrf_score"])) > 1e-12:
            raise RuntimeError(f"{row['id']} RRF分数重算不一致")
        for field in ("dense_rank", "bm25_rank"):
            if observed.get(field) != wanted.get(field):
                raise RuntimeError(f"{row['id']} RRF来源rank不一致：{field}")


def _quality_tuple(candidate: dict[str, Any]) -> tuple[float, ...]:
    return (
        float(candidate["all_gold_docs_recalled_rate"]),
        float(candidate["evidence_coverage"]),
        float(candidate["document_recall"]),
        float(candidate["multi_document_complete_recall_rate"]),
        float(candidate["document_hit_rate"]),
        float(candidate["document_mrr"]),
    )


def _coordinate_select(
    evaluations: list[dict[str, Any]],
    *,
    parameter: str,
    default_value: float | int,
) -> dict[str, Any]:
    eligible = [row for row in evaluations if row["quality_gate"]["passed"]]
    pool = eligible or evaluations
    winner = max(
        pool,
        key=lambda row: (
            _quality_tuple(row["candidate_result"]),
            -abs(float(row[parameter]) - float(default_value)),
            -float(row[parameter]),
        ),
    )
    return {
        "parameter": parameter,
        "selected_configuration": winner["configuration"],
        "selected_value": winner[parameter],
        "selection_pool": "dense_quality_gate_passed" if eligible else "best_available_no_gate_pass",
        "eligible_configurations": [row["configuration"] for row in eligible],
        "selection_order": [
            "all_gold", "evidence", "recall", "multi_document_complete",
            "hit", "mrr", "closer_to_default_on_tie", "smaller_value_on_tie",
        ],
    }


def _strict_quality_improvement(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> bool:
    return any(
        float(candidate[metric]) > float(baseline[metric]) + 1e-12
        for metric in HARD_QUALITY_METRICS
    )


def _final_select(
    evaluations: list[dict[str, Any]], baseline: dict[str, Any]
) -> dict[str, Any]:
    eligible = [row for row in evaluations if row["quality_gate"]["passed"]]
    if not eligible:
        return {
            "selected_configuration": None,
            "promoted_configuration": None,
            "recommendation": "retain_dense_d20_r20",
            "reason": "no_hybrid_configuration_passed_dense_quality_gate",
            "eligible_configurations": [],
        }
    winner = max(
        eligible,
        key=lambda row: (
            _quality_tuple(row["candidate_result"]),
            -float(row["bm25_k"]),
            -abs(float(row["k1"]) - DEFAULT_K1),
            -abs(float(row["b"]) - DEFAULT_B),
            -abs(float(row["rrf_k"]) - DEFAULT_RRF_K),
        ),
    )
    improved = _strict_quality_improvement(baseline, winner["candidate_result"])
    return {
        "selected_configuration": winner["configuration"],
        "promoted_configuration": winner["configuration"] if improved else None,
        "recommendation": (
            "hybrid_candidate_requires_limited_d_recheck"
            if improved else "retain_dense_d20_r20_quality_tie"
        ),
        "strict_quality_improvement": improved,
        "eligible_configurations": [row["configuration"] for row in eligible],
        "selection_order": [
            "hard_quality_gate", "all_gold", "evidence", "recall",
            "multi_document_complete", "hit", "mrr", "smaller_bm25_pool_on_tie",
            "closer_to_defaults_on_tie",
        ],
    }


def _compact_evaluation(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "candidate_result"}


def _render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# V3-5 B2 Hybrid BM25/RRF扫描报告",
        "",
        f"- run_id：`{payload['run_id']}`",
        f"- development：{payload['question_count']}题",
        f"- 真实检索：{payload['real_retrieval_query_count']}次（含1次预热）",
        f"- wall：{payload['wall_s']:.3f}s",
        "",
        "## 配置结果",
        "",
        "| 阶段 | 配置 | BM25 K | k1 | b | RRF | Recall | all-gold | Evidence | 回归/混合 | mean(s) |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["evaluations"]:
        quality = row["quality"]
        comparison = row["comparison"]
        lines.append(
            f"| {row['coordinate']} | {row['configuration']} | {row['bm25_k']} | "
            f"{row['k1']} | {row['b']} | {row['rrf_k']} | "
            f"{quality['document_recall']:.4f} | {quality['all_gold_docs_recalled_rate']:.4f} | "
            f"{quality['evidence_coverage']:.4f} | "
            f"{len(comparison['answerable_regressed_ids'])}/{len(comparison['answerable_mixed_ids'])} | "
            f"{row['latency']['mean']:.4f} |"
        )
    lines.extend([
        "",
        "## 选择",
        "",
        f"- TopK坐标：`{payload['coordinate_selections']['bm25_k']['selected_configuration']}`",
        f"- k1坐标：`{payload['coordinate_selections']['k1']['selected_configuration']}`",
        f"- b坐标：`{payload['coordinate_selections']['b']['selected_configuration']}`",
        f"- RRF坐标：`{payload['coordinate_selections']['rrf_k']['selected_configuration']}`",
        f"- 最终候选：`{payload['final_selection']['selected_configuration']}`",
        f"- 可推广候选：`{payload['final_selection']['promoted_configuration']}`",
        f"- 建议：`{payload['final_selection']['recommendation']}`",
        "",
        "## 控制与边界",
        "",
        f"- Hybrid control稳定：{payload['control_stability']['hybrid']['passed']}",
        f"- Dense control稳定：{payload['control_stability']['dense']['passed']}",
        f"- GPU：{payload['runtime']['runtime']['embedding']['actual_device']} / {payload['runtime']['runtime']['reranker']['actual_device']}",
        "- 本报告只使用development，不调用LLM，不修改生产默认值。",
    ])
    return "\n".join(lines) + "\n"


def _render_conclusion(payload: dict[str, Any]) -> str:
    selection = payload["final_selection"]
    selected = next(
        (row for row in payload["evaluations"] if row["configuration"] == selection["selected_configuration"]),
        None,
    )
    lines = [
        "# V3-5 B2 Hybrid BM25/RRF结论",
        "",
        "## 结论",
        "",
        f"- 建议：`{selection['recommendation']}`。",
        f"- 通过Dense质量门槛的最佳Hybrid配置：`{selection['selected_configuration']}`。",
        f"- 需要有限复核D的可推广配置：`{selection['promoted_configuration']}`。",
    ]
    if selected is not None:
        quality = selected["quality"]
        comparison = selected["comparison"]
        lines.extend([
            f"- 参数：BM25 K{selected['bm25_k']}、k1={selected['k1']}、b={selected['b']}、RRF={selected['rrf_k']}；Dense/Reranker固定20/20。",
            f"- 质量：Hit {quality['document_hit_rate']:.4f}、Recall {quality['document_recall']:.4f}、MRR {quality['document_mrr']:.4f}、all-gold {quality['all_gold_docs_recalled_rate']:.4f}、Evidence {quality['evidence_coverage']:.4f}。",
            f"- 相对同期Dense control：改善{len(comparison['answerable_improved_ids'])}题、回归{len(comparison['answerable_regressed_ids'])}题、混合损失{len(comparison['answerable_mixed_ids'])}题。",
        ])
    lines.extend([
        "",
        "## 解释边界",
        "",
        "- 这是坐标扫描，不是k1×b×RRF×TopK笛卡尔积，不能证明全局最优。",
        "- BM25分数不跨问题归一化，本阶段只用其排序进入RRF，不把绝对分数当拒答阈值。",
        "- 单轮时延只作描述；F阶段才做交替重复性能验收。",
        "- 若出现可推广Hybrid候选，只标记需要有限复核D，不在本阶段自动重跑D或进入E。",
        "- 题库仍为draft；validation/frozen_test未使用；生产默认值未改变。",
    ])
    return "\n".join(lines) + "\n"


def _production_snapshot_check() -> dict[str, Any]:
    from eval.crud_rag_1000.v3_1.execute_frozen_baseline import snapshot

    preflight = runner._load_json(runner.SOURCE_AUDIT / "preflight.json")
    expected = preflight["snapshots"]["production"]
    protected = [
        PROJECT_ROOT / ".env",
        PROJECT_ROOT / "data/metadata.db",
        PROJECT_ROOT / "data/metadata.db-wal",
        PROJECT_ROOT / "data/metadata.db-shm",
        PROJECT_ROOT / "data/chroma_db",
        PROJECT_ROOT / "data/uploads",
    ]
    current = snapshot(protected)
    changed = sorted(set(expected) | set(current))
    changed = [key for key in changed if expected.get(key) != current.get(key)]
    return {"files_checked": len(expected), "files_changed": changed}


def run_phase_b2(
    experiment_id: str,
    run_id: str,
    *,
    junit_path: Path | None = None,
) -> dict[str, Any]:
    experiment, protocol = runner._load_protocol(experiment_id)
    storage_before = runner._validate_experiment_storage(experiment, protocol)
    d_root = experiment / "phase-d" / "v35-d-rerank-context-quality-20260830"
    d_verification = runner._load_json(d_root / "phase-d-final-verification.json")
    d_summary = runner._load_json(d_root / "phase-d-summary.json")
    if d_verification.get("status") != "passed":
        raise RuntimeError("D阶段最终验收未通过，不能执行B2")
    if (
        d_summary.get("reranker_selection", {}).get("selected_dense_k") != DENSE_K
        or d_summary.get("reranker_selection", {}).get("selected_reranker_k") != RERANKER_K
    ):
        raise RuntimeError("D阶段没有选择Dense20/Reranker20，B2协议需要重新确认")

    run_id = runner._safe_name(run_id, "run_id")
    phase_root = runner._assert_within(experiment / "phase-b2" / run_id, experiment)
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
        "phase": "V3-5-B2",
        "status": "running",
        "run_id": run_id,
        "created_at": runner._now(),
        "parent_experiment_id": experiment_id,
        "parent_protocol_identity": protocol["protocol_identity"],
        "dataset_sha256": protocol["dataset"]["sha256"],
        "storage_identity": protocol["source"]["storage_identity"],
        "question_count": len(questions),
        "route": "hybrid_bm25_rrf_coordinate_sweep",
        "coordinates": {
            "bm25_top_k": list(BM25_K_VALUES),
            "bm25_k1": list(K1_VALUES),
            "bm25_b": list(B_VALUES),
            "rrf_k": list(RRF_VALUES),
        },
        "fixed_parameters": {
            "mode": "hybrid",
            "dense_candidate_k": DENSE_K,
            "dense_distance_threshold": DISTANCE,
            "reranker_candidate_k": RERANKER_K,
            "final_top_k": FINAL_K,
            "max_chunks_per_document": DOC_CAP,
        },
        "control_strategy": "same_process_hybrid_default_and_dense_d20_r20_pre_post",
        "runner_sha256": runner._sha256_file(Path(__file__).resolve()),
        "retrieval_code_identity": runner._retrieval_code_identity(),
        "b2_code_identity": _b2_code_identity(),
        "dependency_versions": _dependency_versions(),
        "environment": protocol["environment"],
        "network_policy": protocol["network_policy"],
        "llm_policy": protocol["llm_policy"],
        "limits": [
            "development only", "coordinate search rather than cartesian product",
            "no production settings change", "no generation model", "no E-stage gate",
            "no automatic D recheck", "latency is descriptive only",
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
    from backend.services import sparse_retriever
    from backend.services.vector_store import get_index_revision
    from eval.retrieval_sweep import build_candidate_result

    adapters.assert_isolation()
    hybrid_control = _hybrid_config()
    dense_control = _dense_control_config()
    warmup = adapters.retrieve_only(
        questions[0]["question"],
        include_trace=True,
        runtime_config=runner._runtime_config(hybrid_control),
    )
    if warmup["trace"]["effective_config"] != runner._effective_config(hybrid_control):
        raise RuntimeError("B2预热参数未实际生效")
    runner._write_json_exclusive(phase_root / "warmup.json", {
        "id": questions[0]["id"],
        "retrieval_s": warmup["retrieval_s"],
        "effective_config": warmup["trace"]["effective_config"],
        "not_in_quality_metrics": True,
    })

    config_records: dict[str, dict[str, Any]] = {}
    config_id_to_name: dict[str, str] = {}
    pass_rows: dict[str, list[dict[str, Any]]] = {}
    pass_candidates: dict[str, dict[str, Any]] = {}
    pass_latencies: dict[str, dict[str, Any]] = {}
    first_pass_by_config_id: dict[str, str] = {}
    actual_schedule: list[dict[str, str]] = []
    evaluation_by_config_id: dict[str, dict[str, Any]] = {}
    bm25_index_prewarms: list[dict[str, Any]] = []
    last_bm25_index_key: tuple[float, float] | None = None

    def register(config: dict[str, Any], role: str) -> str:
        config_id = runner._config_id(config)
        if config_id in config_id_to_name:
            return config_id_to_name[config_id]
        name = "dense-d20-r20" if config["mode"] == "dense" else _config_name(config)
        record = {
            "name": name,
            "role": role,
            "config_id": config_id,
            "retrieval": config,
            "effective_config": runner._effective_config(config),
            "derived": {
                "fusion_pool_k": runner._fusion_pool_k(config) if config["mode"] == "hybrid" else None,
                "actual_dense_request_k": config["dense_candidate_k"],
                "actual_bm25_request_k": config["bm25_top_k"] if config["mode"] == "hybrid" else None,
                "reranker_input_cap": config["reranker_candidate_k"],
            },
        }
        config_id_to_name[config_id] = name
        config_records[name] = record
        runner._write_json_exclusive(phase_root / "configs" / f"{name}.json", record)
        return name

    def execute(config: dict[str, Any], label: str, *, force: bool = False) -> str:
        nonlocal last_bm25_index_key
        config_id = runner._config_id(config)
        name = register(config, label)
        if not force and config_id in first_pass_by_config_id:
            return first_pass_by_config_id[config_id]
        if config["mode"] == "hybrid":
            index_key = (float(config["bm25_k1"]), float(config["bm25_b"]))
            if index_key != last_bm25_index_key:
                prewarm_started = time.perf_counter()
                cache_key_before = repr(sparse_retriever._cache.get("key"))
                rows = sparse_retriever.search(
                    questions[0]["question"],
                    top_k=config["bm25_top_k"],
                    k1=config["bm25_k1"],
                    b=config["bm25_b"],
                )
                bm25_index_prewarms.append({
                    "k1": config["bm25_k1"], "b": config["bm25_b"],
                    "top_k": config["bm25_top_k"], "returned": len(rows),
                    "wall_s": round(time.perf_counter() - prewarm_started, 6),
                    "index_revision": get_index_revision(),
                    "allowed_document_ids": "all",
                    "cache_key_before": cache_key_before,
                    "cache_key_after": repr(sparse_retriever._cache.get("key")),
                    "excluded_from_quality_and_latency": True,
                })
                last_bm25_index_key = index_key
        pass_name = f"{len(actual_schedule) + 1:02d}-{label}"
        actual_schedule.append({"pass": pass_name, "configuration": name})
        print(f"[B2 {len(actual_schedule):02d}] {pass_name}", flush=True)
        rows: list[dict[str, Any]] = []
        path = phase_root / "passes" / f"{pass_name}.jsonl"
        runtime_config = runner._runtime_config(config)
        with path.open("x", encoding="utf-8") as handle:
            for index, question in enumerate(questions, 1):
                result = adapters.retrieve_only(
                    question["question"], include_trace=True, runtime_config=runtime_config
                )
                trace = result.get("trace")
                if trace is None or trace.get("effective_config") != runner._effective_config(config):
                    raise RuntimeError(f"{pass_name}参数未生效或Trace缺失：{question['id']}")
                row = {
                    "id": question["id"], "pass": pass_name, "configuration": name,
                    "config_id": config_id, "retrieval": result["retrieval"],
                    "retrieval_s": result["retrieval_s"], "trace": trace,
                }
                if config["mode"] == "hybrid":
                    _validate_hybrid_trace(row, config)
                else:
                    phase_c._validate_trace(row, config)
                rows.append(row)
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                if index % 12 == 0:
                    print(f"  {index}/{len(questions)}", flush=True)
        candidate = build_candidate_result(
            candidate_k=FINAL_K,
            questions=questions,
            retrievals={row["id"]: row["retrieval"] for row in rows},
            timings_s=[row["retrieval_s"] for row in rows],
            traces={row["id"]: row["trace"] for row in rows},
        )
        pass_rows[pass_name] = rows
        pass_candidates[pass_name] = candidate
        pass_latencies[pass_name] = runner._latency(rows)
        runner._write_json_exclusive(phase_root / "passes" / f"{pass_name}-summary.json", {
            "pass": pass_name, "configuration": name, "config_id": config_id,
            "question_count": len(rows), "quality": phase_b._compact_quality(candidate),
            "stage_quality": phase_b._stage_quality(candidate),
            "candidate_counts": _candidate_counts(rows) if config["mode"] == "hybrid" else None,
            "latency": pass_latencies[pass_name], "candidate_result": candidate,
        })
        first_pass_by_config_id.setdefault(config_id, pass_name)
        return pass_name

    hybrid_pre = execute(hybrid_control, "hybrid-control-pre", force=True)
    dense_pre = execute(dense_control, "dense-control-pre", force=True)
    first_pass_by_config_id[runner._config_id(hybrid_control)] = hybrid_pre
    first_pass_by_config_id[runner._config_id(dense_control)] = dense_pre
    dense_candidate = pass_candidates[dense_pre]
    dense_rows = pass_rows[dense_pre]

    def evaluate(config: dict[str, Any], coordinate: str) -> dict[str, Any]:
        config_id = runner._config_id(config)
        if config_id in evaluation_by_config_id:
            row = evaluation_by_config_id[config_id]
            if coordinate not in row["coordinates_seen"]:
                row["coordinates_seen"].append(coordinate)
            return row
        pass_name = execute(config, _config_name(config))
        candidate = pass_candidates[pass_name]
        comparison = phase_b._compare_to_control(
            dense_candidate, candidate, dense_rows, pass_rows[pass_name]
        )
        gate = phase_d._quality_gate(dense_candidate, candidate, comparison)
        row = {
            "coordinate": coordinate,
            "coordinates_seen": [coordinate],
            "configuration": _config_name(config),
            "pass": pass_name,
            "config_id": config_id,
            "bm25_k": config["bm25_top_k"], "k1": config["bm25_k1"],
            "b": config["bm25_b"], "rrf_k": config["rrf_k"],
            "fusion_pool_k": runner._fusion_pool_k(config),
            "quality": phase_b._compact_quality(candidate),
            "stage_quality": phase_b._stage_quality(candidate),
            "candidate_counts": _candidate_counts(pass_rows[pass_name]),
            "latency": pass_latencies[pass_name],
            "comparison": comparison, "quality_gate": gate,
            "candidate_result": candidate,
        }
        evaluation_by_config_id[config_id] = row
        return row

    topk_rows = [evaluate(_hybrid_config(bm25_k=value), "bm25_k") for value in BM25_K_VALUES]
    topk_selection = _coordinate_select(topk_rows, parameter="bm25_k", default_value=DEFAULT_BM25_K)
    selected_topk = int(topk_selection["selected_value"])

    k1_rows = [
        evaluate(_hybrid_config(bm25_k=selected_topk, k1=value), "k1")
        for value in K1_VALUES
    ]
    k1_selection = _coordinate_select(k1_rows, parameter="k1", default_value=DEFAULT_K1)
    selected_k1 = float(k1_selection["selected_value"])

    b_rows = [
        evaluate(_hybrid_config(bm25_k=selected_topk, k1=selected_k1, b=value), "b")
        for value in B_VALUES
    ]
    b_selection = _coordinate_select(b_rows, parameter="b", default_value=DEFAULT_B)
    selected_b = float(b_selection["selected_value"])

    rrf_rows = [
        evaluate(
            _hybrid_config(bm25_k=selected_topk, k1=selected_k1, b=selected_b, rrf_k=value),
            "rrf_k",
        )
        for value in RRF_VALUES
    ]
    rrf_selection = _coordinate_select(rrf_rows, parameter="rrf_k", default_value=DEFAULT_RRF_K)

    dense_post = execute(dense_control, "dense-control-post", force=True)
    hybrid_post = execute(hybrid_control, "hybrid-control-post", force=True)

    runtime = adapters.get_run_config()
    for component in ("embedding", "reranker"):
        status = runtime["runtime"][component]
        if not (status["loaded"] and status["verified"] and status["actual_device"] == "cuda:0"):
            raise RuntimeError(f"B2阶段{component}未在cuda:0真实运行")
    if "backend.services.llm" in sys.modules:
        raise RuntimeError("B2阶段意外导入LLM模块")
    if blocked:
        raise RuntimeError("B2阶段出现出站网络尝试")

    hybrid_stability = phase_c._control_stability(
        pass_rows[hybrid_pre], pass_rows[hybrid_post],
        ("dense_raw", "dense_eligible", "bm25", "rrf", "reranker", "selected"),
    )
    dense_stability = phase_c._control_stability(
        pass_rows[dense_pre], pass_rows[dense_post],
        ("dense_raw", "dense_eligible", "reranker", "selected"),
    )
    for stability, first, last in (
        (hybrid_stability, hybrid_pre, hybrid_post),
        (dense_stability, dense_pre, dense_post),
    ):
        stability["quality_exact_match"] = (
            phase_b._compact_quality(pass_candidates[first])
            == phase_b._compact_quality(pass_candidates[last])
        )
        stability["passed"] = stability["passed"] and stability["quality_exact_match"]
    if not hybrid_stability["passed"] or not dense_stability["passed"]:
        raise RuntimeError("B2阶段control首尾不稳定，拒绝解释参数收益")

    evaluations = list(evaluation_by_config_id.values())
    cross_config_invariants = _cross_config_invariants(
        dense_control_rows=dense_rows,
        evaluations=evaluations,
        pass_rows=pass_rows,
        topk_evaluations=topk_rows,
        rrf_evaluations=rrf_rows,
    )
    final_selection = _final_select(evaluations, dense_candidate)
    if _b2_code_identity() != phase_protocol["b2_code_identity"]:
        raise RuntimeError("B2运行期间检索/BM25/RRF/Trace代码发生变化")
    storage_after = runner._validate_experiment_storage(experiment, protocol)
    runner._source_artifact_checks(
        runner._storage_manifest(runner.SOURCE_RUN / "chroma", runner.SOURCE_RUN / "metadata.db")
    )
    production = _production_snapshot_check()
    if production["files_changed"]:
        raise RuntimeError("B2阶段生产快照发生变化")

    summary = {
        "schema_version": 1,
        "phase": "V3-5-B2",
        "status": "passed",
        "run_id": run_id,
        "parent_experiment_id": experiment_id,
        "completed_at": runner._now(),
        "wall_s": round(time.perf_counter() - started, 3),
        "question_count": len(questions),
        "unique_hybrid_configuration_count": len(evaluations),
        "real_pass_count": len(actual_schedule),
        "real_quality_query_count": len(questions) * len(actual_schedule),
        "warmup_query_count": 1,
        "real_retrieval_query_count": len(questions) * len(actual_schedule) + 1,
        "bm25_index_prewarm_count": len(bm25_index_prewarms),
        "bm25_index_prewarms": bm25_index_prewarms,
        "actual_schedule": actual_schedule,
        "dense_control": {
            "configuration": "dense-d20-r20",
            "quality": phase_b._compact_quality(dense_candidate),
            "latency": pass_latencies[dense_pre],
        },
        "evaluations": [_compact_evaluation(row) for row in evaluations],
        "coordinate_selections": {
            "bm25_k": topk_selection, "k1": k1_selection,
            "b": b_selection, "rrf_k": rrf_selection,
        },
        "final_selection": final_selection,
        "cross_config_invariants": cross_config_invariants,
        "control_stability": {"hybrid": hybrid_stability, "dense": dense_stability},
        "runtime": runtime,
        "llm_module_imported": False,
        "outbound_ip_connections": blocked,
        "storage_before": storage_before,
        "storage_after": storage_after,
        "sealed_baseline_files_checked": len(
            runner._load_json(runner.SOURCE_AUDIT / "artifact-manifest.json")["files"]
        ),
        "production_snapshot": production,
        "limits": phase_protocol["limits"],
    }
    summary_path = phase_root / "phase-b2-summary.json"
    runner._write_json_exclusive(summary_path, summary)
    report_path = phase_root / "phase-b2-report.md"
    report_path.write_text(_render_report(summary), encoding="utf-8")
    conclusion_path = phase_root / "phase-b2-conclusion.md"
    conclusion_path.write_text(_render_conclusion(summary), encoding="utf-8")
    completed_protocol = phase_root / "protocol-completed.json"
    runner._write_json_exclusive(completed_protocol, {
        **phase_protocol, "status": "passed", "completed_at": summary["completed_at"],
        "actual_schedule": actual_schedule,
        "coordinate_selections": summary["coordinate_selections"],
        "final_selection": final_selection,
    })

    json_errors: list[str] = []
    json_files = list(phase_root.rglob("*.json"))
    jsonl_files = list(phase_root.rglob("*.jsonl"))
    for path in json_files:
        try:
            runner._load_json(path)
        except Exception as exc:  # pragma: no cover - artifact integrity guard
            json_errors.append(f"{path.name}:{type(exc).__name__}")
    for path in jsonl_files:
        try:
            with path.open(encoding="utf-8") as handle:
                parsed = [json.loads(line) for line in handle]
            if len(parsed) != len(questions) or len({row["id"] for row in parsed}) != len(questions):
                json_errors.append(f"{path.name}:row_contract")
        except Exception as exc:  # pragma: no cover - artifact integrity guard
            json_errors.append(f"{path.name}:{type(exc).__name__}")
    if json_errors:
        raise RuntimeError("B2产物JSON/JSONL校验失败：" + ", ".join(json_errors))

    artifacts = {
        "protocol-completed.json": completed_protocol,
        "phase-b2-summary.json": summary_path,
        "phase-b2-report.md": report_path,
        "phase-b2-conclusion.md": conclusion_path,
        "../../../../run_phase_b2_hybrid_bm25_sweep.py": Path(__file__).resolve(),
        "../../../../test_phase_b2_hybrid_bm25_sweep.py": ROOT / "test_phase_b2_hybrid_bm25_sweep.py",
    }
    if junit_path is not None:
        junit_path = junit_path.resolve()
        if not junit_path.is_file():
            raise FileNotFoundError(f"B2最终JUnit不存在：{junit_path}")
        artifacts["../../../../.validation/" + junit_path.name] = junit_path
    verification = {
        "schema_version": 1,
        "phase": "V3-5-B2",
        "status": "passed",
        "verified_at": runner._now(),
        "run_id": run_id,
        "checks": {
            "question_count": len(questions),
            "unique_hybrid_configurations": len(evaluations),
            "real_passes": len(actual_schedule),
            "real_retrieval_queries": summary["real_retrieval_query_count"],
            "bm25_index_prewarms": summary["bm25_index_prewarm_count"],
            "b2_code_identity_unchanged": True,
            "hybrid_control_exact": hybrid_stability["passed"],
            "dense_control_exact": dense_stability["passed"],
            "cross_config_invariants": cross_config_invariants,
            "embedding_actual_device": runtime["runtime"]["embedding"]["actual_device"],
            "reranker_actual_device": runtime["runtime"]["reranker"]["actual_device"],
            "llm_module_imported": False,
            "outbound_ip_connections": 0,
            "database_quick_check": storage_after["database_checks"]["chroma.sqlite3"]["quick_check"],
            "documents": storage_after["database_checks"]["metadata.db"]["documents"],
            "embeddings": storage_after["database_checks"]["chroma.sqlite3"]["embeddings"],
            "sealed_baseline_files_checked": summary["sealed_baseline_files_checked"],
            "production_snapshot_files_checked": production["files_checked"],
            "production_snapshot_files_changed": production["files_changed"],
            "json_files_parsed_before_verification": len(json_files),
            "jsonl_files_parsed": len(jsonl_files),
            "json_or_jsonl_parse_errors": json_errors,
            "final_selection": final_selection,
        },
        "artifacts": {
            name: {"sha256": runner._sha256_file(path), "bytes": path.stat().st_size}
            for name, path in artifacts.items()
        },
        "limits": [
            "dataset remains draft", "coordinate scan is not a cartesian global optimum",
            "latency is descriptive only", "no production default was changed",
            "D recheck and E were not executed",
        ],
    }
    runner._write_json_exclusive(phase_root / "phase-b2-final-verification.json", verification)
    return {"phase_root": str(phase_root), "summary": summary, "verification": verification}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-id", default="v35-a-baseline-20260829")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--junit-path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_phase_b2(
        args.experiment_id,
        args.run_id,
        junit_path=Path(args.junit_path) if args.junit_path else None,
    )
    print(json.dumps({
        "status": result["summary"]["status"],
        "phase_root": result["phase_root"],
        "wall_s": result["summary"]["wall_s"],
        "final_selection": result["summary"]["final_selection"],
        "control_stability": result["summary"]["control_stability"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
