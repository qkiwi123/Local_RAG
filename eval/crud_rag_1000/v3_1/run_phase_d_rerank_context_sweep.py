"""V3-5 D：质量优先的Reranker预算与最终上下文扫描。

本入口复用A阶段复制的隔离索引，只执行development 72题，不调用生成模型，
不修改生产默认值。真实GPU检索用于比较Dense/Reranker输入预算；最终TopK与
单文档配额从完整Reranker序列精确重放，并先验证重放与真实业务结果一致。
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
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from eval.crud_rag_1000.v3_1 import run_phase_b_mode_ablation as phase_b
from eval.crud_rag_1000.v3_1 import run_phase_c_dense_sweep as phase_c
from eval.crud_rag_1000.v3_1 import tuning_runner as runner


BASELINE_DENSE_K = 15
BASELINE_RERANKER_K = 15
BASELINE_FINAL_K = 5
BASELINE_DOC_CAP = 3
BASELINE_DISTANCE = 0.67

# K>15时同步提供足够Dense候选；C已经证明Dense K30/50 + Reranker K15
# 的最终Top5与K15相同，所以锚点可区分前级扩大与真正扩大重排输入。
REAL_CONFIG_SPECS = (
    (15, 15),
    (15, 10),
    (20, 15),
    (20, 20),
    (30, 15),
    (30, 20),
    (30, 30),
    (50, 15),
    (50, 50),
)
FINAL_TOP_K_VALUES = (3, 5, 8, 10)
DOC_CAP_VALUES = (1, 2, 3, 4)
HARD_QUALITY_METRICS = phase_c.HARD_QUALITY_METRICS


def _dense_reranker_config(
    dense_k: int,
    reranker_k: int,
    *,
    final_k: int = BASELINE_FINAL_K,
    doc_cap: int = BASELINE_DOC_CAP,
) -> dict[str, Any]:
    payload = deepcopy(runner.BASELINE_CONFIG)
    payload.update({
        "mode": "dense",
        "dense_candidate_k": dense_k,
        "dense_distance_threshold": BASELINE_DISTANCE,
        "reranker_enabled": True,
        "reranker_candidate_k": reranker_k,
        "final_top_k": final_k,
        "max_chunks_per_document": doc_cap,
    })
    return runner._validated_config_payload(payload)


def _hybrid_control_config() -> dict[str, Any]:
    return runner._validated_config_payload(deepcopy(runner.BASELINE_CONFIG))


def _config_name(dense_k: int, reranker_k: int) -> str:
    return f"dense-k{dense_k}-rerank-k{reranker_k}"


def _context_name(final_k: int, doc_cap: int) -> str:
    return f"top{final_k}-doccap{doc_cap}"


def _stage_candidates(row: dict[str, Any], stage: str) -> list[dict[str, Any]]:
    return phase_c._stage_candidates(row, stage)


def _candidate_counts(rows: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    return phase_c._candidate_counts(rows)


def _hydrate_reranker_stage(
    row: dict[str, Any],
    chunks: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    fields = (
        "distance", "dense_rank", "bm25_rank", "bm25_score", "rrf_score",
        "reranker_score",
    )
    hydrated: list[dict[str, Any]] = []
    for candidate in _stage_candidates(row, "reranker"):
        chunk_id = candidate["chunk_id"]
        base = chunks.get(chunk_id)
        if base is None:
            raise RuntimeError(f"Reranker候选无法从隔离Chroma回查：{chunk_id}")
        result = {
            "chunk_id": chunk_id,
            "content": base.get("content", ""),
            "metadata": dict(base.get("metadata") or {}),
        }
        for field in fields:
            if candidate.get(field) is not None:
                result[field] = candidate[field]
        hydrated.append(result)
    return hydrated


def _limit_per_document(
    rows: list[dict[str, Any]],
    *,
    top_k: int,
    max_per_document: int,
) -> list[dict[str, Any]]:
    document_counts: dict[str, int] = {}
    selected: list[dict[str, Any]] = []
    for row in rows:
        metadata = row.get("metadata") or {}
        document_key = metadata.get("document_id") or metadata.get(
            "document_name", "unknown"
        )
        if document_counts.get(document_key, 0) < max_per_document:
            selected.append(row)
            document_counts[document_key] = document_counts.get(document_key, 0) + 1
    return selected[:top_k]


def _replay_rows(
    rows: list[dict[str, Any]],
    chunks: dict[str, dict[str, Any]],
    *,
    top_k: int,
    doc_cap: int,
) -> list[dict[str, Any]]:
    replayed = []
    for row in rows:
        retrieval = _limit_per_document(
            _hydrate_reranker_stage(row, chunks),
            top_k=top_k,
            max_per_document=doc_cap,
        )
        replayed.append({"id": row["id"], "retrieval": retrieval})
    return replayed


def _validate_trace(
    row: dict[str, Any],
    config: dict[str, Any],
    chunks: dict[str, dict[str, Any]],
) -> None:
    phase_c._validate_trace(row, config)
    replay = _limit_per_document(
        _hydrate_reranker_stage(row, chunks),
        top_k=config["final_top_k"],
        max_per_document=config["max_chunks_per_document"],
    )
    if [item["chunk_id"] for item in replay] != [
        item["chunk_id"] for item in row["retrieval"]
    ]:
        raise RuntimeError(f"{row['id']} 本地配额重放与真实selected不一致")


def _quality_gate(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    comparison: dict[str, Any],
) -> dict[str, Any]:
    return phase_c._quality_gate(baseline, candidate, comparison)


def _quality_score(
    candidate: dict[str, Any],
    *,
    dense_k: int,
    reranker_k: int,
) -> tuple[float, ...]:
    return (
        float(candidate["all_gold_docs_recalled_rate"]),
        float(candidate["evidence_coverage"]),
        float(candidate["document_recall"]),
        float(candidate["multi_document_complete_recall_rate"]),
        float(candidate["document_hit_rate"]),
        float(candidate["document_mrr"]),
        -float(reranker_k),
        -float(dense_k),
    )


def _select_reranker(evaluations: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [row for row in evaluations if row["quality_gate"]["passed"]]
    if not eligible:
        raise RuntimeError("D阶段真实配置没有通过质量门槛，基准应始终通过")
    winner = max(
        eligible,
        key=lambda row: _quality_score(
            row["candidate_result"],
            dense_k=row["dense_k"],
            reranker_k=row["reranker_k"],
        ),
    )
    return {
        "selected_configuration": winner["configuration"],
        "selected_dense_k": winner["dense_k"],
        "selected_reranker_k": winner["reranker_k"],
        "eligible_configurations": [row["configuration"] for row in eligible],
        "selection_order": [
            "all_gold", "evidence", "recall", "multi_document_complete",
            "hit", "mrr", "smaller_reranker_k_on_quality_tie",
            "smaller_dense_k_on_quality_tie",
        ],
    }


def _distribution(values: list[float | int]) -> dict[str, float | int | None]:
    if not values:
        return {"min": None, "max": None, "mean": None, "p50": None, "p95": None, "sum": 0}
    ordered = sorted(values)
    return {
        "min": min(values),
        "max": max(values),
        "mean": round(statistics.mean(values), 4),
        "p50": ordered[round(0.50 * (len(ordered) - 1))],
        "p95": ordered[round(0.95 * (len(ordered) - 1))],
        "sum": sum(values),
    }


def _context_budget(rows: list[dict[str, Any]]) -> dict[str, Any]:
    chunk_counts: list[int] = []
    document_counts: list[int] = []
    char_counts: list[int] = []
    token_counts: list[int] = []
    for row in rows:
        retrieval = row["retrieval"]
        chunk_counts.append(len(retrieval))
        document_counts.append(len({
            (item.get("metadata") or {}).get("document_id")
            or (item.get("metadata") or {}).get("document_name", "unknown")
            for item in retrieval
        }))
        char_counts.append(sum(
            int((item.get("metadata") or {}).get("char_count") or len(item.get("content", "")))
            for item in retrieval
        ))
        token_counts.append(sum(
            int((item.get("metadata") or {}).get("token_count") or 0)
            for item in retrieval
        ))
    return {
        "chunk_count": _distribution(chunk_counts),
        "distinct_document_count": _distribution(document_counts),
        "content_char_count": _distribution(char_counts),
        "ingest_token_count": _distribution(token_counts),
        "token_note": "sum of stored per-chunk embedding token_count; excludes prompt labels",
    }


def _build_candidate(
    questions: list[dict[str, Any]],
    replay_rows: list[dict[str, Any]],
    *,
    candidate_k: int,
) -> dict[str, Any]:
    from eval.retrieval_sweep import build_candidate_result

    return build_candidate_result(
        candidate_k=candidate_k,
        questions=questions,
        retrievals={row["id"]: row["retrieval"] for row in replay_rows},
        timings_s=[],
    )


def _context_score(row: dict[str, Any]) -> tuple[float, ...]:
    quality = row["candidate_result"]
    budget = row["context_budget"]
    return (
        float(quality["all_gold_docs_recalled_rate"]),
        float(quality["evidence_coverage"]),
        float(quality["document_recall"]),
        float(quality["multi_document_complete_recall_rate"]),
        float(quality["document_hit_rate"]),
        float(quality["document_mrr"]),
        -float(budget["ingest_token_count"]["mean"] or 0),
        -float(row["final_top_k"]),
        -float(row["doc_cap"]),
    )


def _select_context(evaluations: list[dict[str, Any]], baseline: dict[str, Any]) -> dict[str, Any]:
    eligible = [row for row in evaluations if row["quality_gate"]["passed"]]
    if not eligible:
        raise RuntimeError("最终上下文扫描没有通过质量门槛的配置")
    quality_winner = max(eligible, key=_context_score)
    cost_winner = min(
        eligible,
        key=lambda row: (
            float(row["context_budget"]["ingest_token_count"]["mean"] or 0),
            float(row["context_budget"]["content_char_count"]["mean"] or 0),
            row["final_top_k"],
            row["doc_cap"],
        ),
    )
    quality_improved = any(
        float(quality_winner["candidate_result"][metric])
        > float(baseline[metric]) + 1e-12
        for metric in HARD_QUALITY_METRICS
    )
    return {
        "selected_quality_configuration": quality_winner["configuration"],
        "selected_cost_balanced_configuration": cost_winner["configuration"],
        "quality_improved_over_top5_baseline": quality_improved,
        "eligible_configurations": [row["configuration"] for row in eligible],
        "selection_order": [
            "all_gold", "evidence", "recall", "multi_document_complete",
            "hit", "mrr", "smaller_context_on_quality_tie",
        ],
    }


def _render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# V3-5 D 重排预算与最终上下文结果",
        "",
        f"- Run：`{payload['run_id']}`",
        "- 范围：development 72题（60可回答+12不可回答）",
        "- 路线：Dense+Reranker质量优先；distance 0.67",
        "- 执行：同一GPU进程真实扫描重排输入；最终TopK/配额精确重放；无生成模型调用",
        "",
        "## 真实重排预算",
        "",
        "| 配置 | Dense K | Reranker K | Recall | all-gold | 多文档完整 | Evidence | 回归/混合 | mean(s) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["reranker_evaluations"]:
        quality = row["quality"]
        comparison = row["comparison"]
        lines.append(
            f"| {row['configuration']} | {row['dense_k']} | {row['reranker_k']} | "
            f"{quality['document_recall']:.4f} | {quality['all_gold_docs_recalled_rate']:.4f} | "
            f"{quality['multi_document_complete_recall_rate']:.4f} | {quality['evidence_coverage']:.4f} | "
            f"{len(comparison['answerable_regressed_ids'])}/{len(comparison['answerable_mixed_ids'])} | "
            f"{row['latency']['mean']:.4f} |"
        )
    lines += [
        "",
        "## 最终上下文重放",
        "",
        "| 配置 | Recall | all-gold | 多文档完整 | Evidence | chunks mean | tokens mean | 门槛 |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in payload["context_evaluations"]:
        quality = row["quality"]
        budget = row["context_budget"]
        lines.append(
            f"| {row['configuration']} | {quality['document_recall']:.4f} | "
            f"{quality['all_gold_docs_recalled_rate']:.4f} | "
            f"{quality['multi_document_complete_recall_rate']:.4f} | "
            f"{quality['evidence_coverage']:.4f} | "
            f"{budget['chunk_count']['mean']:.4f} | "
            f"{budget['ingest_token_count']['mean']:.2f} | "
            f"{'通过' if row['quality_gate']['passed'] else '不通过'} |"
        )
    selection = payload["final_selection"]
    lines += [
        "",
        "## 选择结果",
        "",
        f"- 重排预算候选：`{payload['reranker_selection']['selected_configuration']}`",
        f"- 质量优先上下文：`{selection['selected_quality_configuration']}`",
        f"- 成本平衡上下文：`{selection['selected_cost_balanced_configuration']}`",
        f"- 相对Top5基线硬质量有提升：{selection['quality_improved_over_top5_baseline']}",
        "- Top8/Top10若更高属于上下文预算扩大，不记作同预算算法收益。",
        "- 本阶段不修改生产默认值，不运行E，不实现拒答门控。",
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


def run_phase_d(experiment_id: str, run_id: str) -> dict[str, Any]:
    experiment, protocol = runner._load_protocol(experiment_id)
    storage_before = runner._validate_experiment_storage(experiment, protocol)
    phase_c_root = (
        experiment / "phase-c" / "v35-c-dense-quality-20260829"
    )
    phase_c_verification = phase_c_root / "phase-c-final-verification.json"
    phase_c_summary_path = phase_c_root / "phase-c-summary.json"
    if not phase_c_verification.is_file() or runner._load_json(phase_c_verification).get("status") != "passed":
        raise RuntimeError("C阶段最终验收未通过，不能进入D")
    phase_c_summary = runner._load_json(phase_c_summary_path)
    c_selection = phase_c_summary.get("final_selection", {})
    if (
        c_selection.get("selected_dense_k") != BASELINE_DENSE_K
        or abs(float(c_selection.get("selected_distance", -1)) - BASELINE_DISTANCE) > 1e-9
    ):
        raise RuntimeError("C阶段选择不是Dense K15/0.67，D协议需要重新确认")

    run_id = runner._safe_name(run_id, "run_id")
    phase_root = runner._assert_within(experiment / "phase-d" / run_id, experiment)
    phase_root.mkdir(parents=True, exist_ok=False)
    (phase_root / "configs").mkdir()
    (phase_root / "passes").mkdir()
    (phase_root / "replays").mkdir()

    dataset = runner._dataset()
    if runner._sha256_file(runner.DATASET_PATH) != protocol["dataset"]["sha256"]:
        raise RuntimeError("题库hash与A协议不一致")
    questions = runner._split_questions(dataset, "development")
    if [row["id"] for row in questions] != protocol["splits"]["development"]:
        raise RuntimeError("development题目ID与A协议不一致")

    phase_protocol = {
        "schema_version": 1,
        "phase": "V3-5-D",
        "status": "running",
        "run_id": run_id,
        "created_at": runner._now(),
        "parent_experiment_id": experiment_id,
        "parent_protocol_identity": protocol["protocol_identity"],
        "dataset_sha256": protocol["dataset"]["sha256"],
        "storage_identity": protocol["source"]["storage_identity"],
        "question_count": len(questions),
        "route": "quality_first_dense_reranker_context",
        "real_candidate_grid": [
            {"dense_k": dense_k, "reranker_k": reranker_k}
            for dense_k, reranker_k in REAL_CONFIG_SPECS
        ],
        "replay_grid": {
            "final_top_k": list(FINAL_TOP_K_VALUES),
            "max_chunks_per_document": list(DOC_CAP_VALUES),
            "legal_rule": "doc_cap <= final_top_k",
        },
        "fixed_parameters": {
            "mode": "dense",
            "dense_distance_threshold": BASELINE_DISTANCE,
            "real_run_final_top_k": BASELINE_FINAL_K,
            "real_run_max_chunks_per_document": BASELINE_DOC_CAP,
            "bm25_and_rrf": "inactive_in_dense_mode",
        },
        "control_strategy": "same_process_hybrid_and_dense_pre_post",
        "runner_sha256": runner._sha256_file(Path(__file__).resolve()),
        "retrieval_code_identity": runner._retrieval_code_identity(),
        "environment": protocol["environment"],
        "network_policy": protocol["network_policy"],
        "llm_policy": protocol["llm_policy"],
        "limits": [
            "development only",
            "no production settings change",
            "no generation model",
            "no E-stage refusal gate",
            "TopK gains are reported as budget expansion, not same-budget algorithm gains",
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
    from backend.services.vector_store import get_all_chunks
    from eval.retrieval_sweep import build_candidate_result

    adapters.assert_isolation()
    hybrid = _hybrid_control_config()
    baseline = _dense_reranker_config(BASELINE_DENSE_K, BASELINE_RERANKER_K)
    warmup = adapters.retrieve_only(
        questions[0]["question"],
        include_trace=True,
        runtime_config=runner._runtime_config(hybrid),
    )
    if warmup["trace"]["effective_config"] != runner._effective_config(hybrid):
        raise RuntimeError("D预热control参数未实际生效")
    runner._write_json_exclusive(phase_root / "warmup.json", {
        "id": questions[0]["id"],
        "retrieval_s": warmup["retrieval_s"],
        "effective_config": warmup["trace"]["effective_config"],
        "not_in_quality_metrics": True,
    })
    all_chunks = {row["chunk_id"]: row for row in get_all_chunks()}
    if len(all_chunks) != 2914:
        raise RuntimeError(f"隔离Chroma块数异常：{len(all_chunks)}")

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
                "fusion_pool_k": None,
                "fusion_pool_k_applicable": False,
                "actual_dense_request_k": config["dense_candidate_k"],
                "reranker_input_cap": config["reranker_candidate_k"],
            },
        }
        config_ids[config_id] = name
        config_records[name] = record
        runner._write_json_exclusive(phase_root / "configs" / f"{name}.json", record)

    register("hybrid-control", hybrid)
    baseline_name = _config_name(BASELINE_DENSE_K, BASELINE_RERANKER_K)
    register(baseline_name, baseline)
    for dense_k, reranker_k in REAL_CONFIG_SPECS:
        register(_config_name(dense_k, reranker_k), _dense_reranker_config(dense_k, reranker_k))

    pass_rows: dict[str, list[dict[str, Any]]] = {}
    pass_candidates: dict[str, dict[str, Any]] = {}
    pass_latencies: dict[str, dict[str, Any]] = {}
    config_pass: dict[str, str] = {}
    actual_schedule: list[dict[str, str]] = []

    def execute(pass_name: str, config_name: str, config: dict[str, Any]) -> None:
        actual_schedule.append({"pass": pass_name, "configuration": config_name})
        print(f"[D {len(actual_schedule):02d}] {pass_name}", flush=True)
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
                _validate_trace(row, config, all_chunks)
                rows.append(row)
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                if index % 12 == 0:
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
        pass_latencies[pass_name] = runner._latency(rows)
        runner._write_json_exclusive(phase_root / "passes" / f"{pass_name}-summary.json", {
            "pass": pass_name,
            "configuration": config_name,
            "config_id": runner._config_id(config),
            "question_count": len(rows),
            "quality": phase_b._compact_quality(candidate),
            "stage_quality": phase_b._stage_quality(candidate),
            "candidate_counts": _candidate_counts(rows),
            "latency": pass_latencies[pass_name],
            "candidate_result": candidate,
        })

    execute("01-hybrid-control-pre", "hybrid-control", hybrid)
    execute("02-dense-baseline-pre", baseline_name, baseline)
    pass_number = 3
    for dense_k, reranker_k in REAL_CONFIG_SPECS:
        if (dense_k, reranker_k) == (BASELINE_DENSE_K, BASELINE_RERANKER_K):
            config_pass[_config_name(dense_k, reranker_k)] = "02-dense-baseline-pre"
            continue
        name = _config_name(dense_k, reranker_k)
        pass_name = f"{pass_number:02d}-{name}"
        execute(pass_name, name, _dense_reranker_config(dense_k, reranker_k))
        config_pass[name] = pass_name
        pass_number += 1
    execute("11-dense-baseline-post", baseline_name, baseline)
    execute("12-hybrid-control-post", "hybrid-control", hybrid)

    runtime = adapters.get_run_config()
    for component in ("embedding", "reranker"):
        status = runtime["runtime"][component]
        if not (status["loaded"] and status["verified"] and status["actual_device"] == "cuda:0"):
            raise RuntimeError(f"D阶段{component}未在cuda:0真实运行")
    if "backend.services.llm" in sys.modules:
        raise RuntimeError("D阶段意外导入LLM模块")
    if blocked:
        raise RuntimeError("D阶段出现出站网络尝试")

    hybrid_stability = phase_c._control_stability(
        pass_rows["01-hybrid-control-pre"],
        pass_rows["12-hybrid-control-post"],
        ("dense_raw", "dense_eligible", "bm25", "rrf", "reranker", "selected"),
    )
    dense_stability = phase_c._control_stability(
        pass_rows["02-dense-baseline-pre"],
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
        raise RuntimeError("D阶段control首尾不稳定，拒绝解释参数收益")

    baseline_rows = pass_rows["02-dense-baseline-pre"]
    baseline_candidate = pass_candidates["02-dense-baseline-pre"]
    reranker_evaluations: list[dict[str, Any]] = []
    for dense_k, reranker_k in REAL_CONFIG_SPECS:
        name = _config_name(dense_k, reranker_k)
        pass_name = config_pass[name]
        candidate = pass_candidates[pass_name]
        comparison = phase_b._compare_to_control(
            baseline_candidate, candidate, baseline_rows, pass_rows[pass_name]
        )
        reranker_evaluations.append({
            "configuration": name,
            "pass": pass_name,
            "config_id": config_records[name]["config_id"],
            "dense_k": dense_k,
            "reranker_k": reranker_k,
            "quality": phase_b._compact_quality(candidate),
            "stage_quality": phase_b._stage_quality(candidate),
            "candidate_counts": _candidate_counts(pass_rows[pass_name]),
            "latency": pass_latencies[pass_name],
            "p95_cost_warning_over_1_5x_control": (
                float(pass_latencies[pass_name]["p95"])
                > float(pass_latencies["02-dense-baseline-pre"]["p95"]) * 1.5
            ),
            "comparison": comparison,
            "quality_gate": _quality_gate(baseline_candidate, candidate, comparison),
            "candidate_result": candidate,
        })
    reranker_selection = _select_reranker(reranker_evaluations)
    selected_real = next(
        row for row in reranker_evaluations
        if row["configuration"] == reranker_selection["selected_configuration"]
    )
    selected_rows = pass_rows[selected_real["pass"]]

    context_evaluations: list[dict[str, Any]] = []
    for final_k in FINAL_TOP_K_VALUES:
        for doc_cap in DOC_CAP_VALUES:
            if doc_cap > final_k or final_k > selected_real["reranker_k"]:
                continue
            name = _context_name(final_k, doc_cap)
            replay_rows = _replay_rows(
                selected_rows, all_chunks, top_k=final_k, doc_cap=doc_cap
            )
            candidate = _build_candidate(questions, replay_rows, candidate_k=final_k)
            comparison = phase_b._compare_to_control(
                baseline_candidate, candidate, baseline_rows, replay_rows
            )
            normalized_rows = [
                {"id": row["id"], "retrieval": row["retrieval"][:BASELINE_FINAL_K]}
                for row in replay_rows
            ]
            normalized = _build_candidate(
                questions, normalized_rows, candidate_k=BASELINE_FINAL_K
            )
            normalized_comparison = phase_b._compare_to_control(
                baseline_candidate, normalized, baseline_rows, normalized_rows
            )
            replay_payload = {
                "configuration": name,
                "source_real_configuration": selected_real["configuration"],
                "final_top_k": final_k,
                "max_chunks_per_document": doc_cap,
                "rows": replay_rows,
            }
            runner._write_json_exclusive(phase_root / "replays" / f"{name}.json", replay_payload)
            context_evaluations.append({
                "configuration": name,
                "final_top_k": final_k,
                "doc_cap": doc_cap,
                "quality": phase_b._compact_quality(candidate),
                "normalized_at_5_quality": phase_b._compact_quality(normalized),
                "comparison": comparison,
                "normalized_at_5_comparison": normalized_comparison,
                "quality_gate": _quality_gate(baseline_candidate, candidate, comparison),
                "normalized_at_5_quality_gate": _quality_gate(
                    baseline_candidate, normalized, normalized_comparison
                ),
                "context_budget": _context_budget(replay_rows),
                "candidate_result": candidate,
            })
    final_selection = _select_context(context_evaluations, baseline_candidate)

    storage_after = runner._validate_experiment_storage(experiment, protocol)
    summary = {
        "schema_version": 1,
        "phase": "V3-5-D",
        "status": "passed",
        "run_id": run_id,
        "parent_experiment_id": experiment_id,
        "completed_at": runner._now(),
        "wall_s": round(time.perf_counter() - started, 3),
        "question_count": len(questions),
        "unique_real_dense_configuration_count": len(REAL_CONFIG_SPECS),
        "real_pass_count": len(actual_schedule),
        "real_quality_query_count": len(questions) * len(actual_schedule),
        "warmup_query_count": 1,
        "real_retrieval_query_count": len(questions) * len(actual_schedule) + 1,
        "context_replay_configuration_count": len(context_evaluations),
        "actual_schedule": actual_schedule,
        "reranker_evaluations": [
            {key: value for key, value in row.items() if key != "candidate_result"}
            for row in reranker_evaluations
        ],
        "reranker_selection": reranker_selection,
        "context_evaluations": [
            {key: value for key, value in row.items() if key != "candidate_result"}
            for row in context_evaluations
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
    runner._write_json_exclusive(phase_root / "phase-d-summary.json", summary)
    (phase_root / "phase-d-report.md").write_text(_render_report(summary), encoding="utf-8")
    runner._write_json_exclusive(phase_root / "protocol-completed.json", {
        **phase_protocol,
        "status": "passed",
        "completed_at": summary["completed_at"],
        "actual_schedule": actual_schedule,
        "reranker_selection": reranker_selection,
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
    result = run_phase_d(args.experiment_id, args.run_id)
    summary = result["summary"]
    print(json.dumps({
        "status": summary["status"],
        "phase_root": result["phase_root"],
        "wall_s": summary["wall_s"],
        "reranker_selection": summary["reranker_selection"],
        "final_selection": summary["final_selection"],
        "control_stability": summary["control_stability"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
