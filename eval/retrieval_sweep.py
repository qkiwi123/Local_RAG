"""无 LLM 的检索候选实验指标、选择规则与 Markdown 报告。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from eval.metrics import (
    aggregate_unanswerable_gates,
    document_metrics,
    evidence_coverage_details,
    exact_evidence_metrics,
)


def _mean(values: list[float]) -> float | None:
    usable = [value for value in values if value is not None]
    return round(sum(usable) / len(usable), 4) if usable else None


def _first_present(mapping: dict, *keys: str, default=None):
    for key in keys:
        if key in mapping:
            return mapping[key]
    return default


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        max(0, round(percentile / 100 * (len(ordered) - 1))),
    )
    return ordered[index]


def _latency(values: list[float]) -> dict:
    if not values:
        return {"mean": 0.0, "p50": 0.0, "p95": 0.0}
    return {
        "mean": round(sum(values) / len(values), 3),
        "p50": round(_percentile(values, 50), 3),
        "p95": round(_percentile(values, 95), 3),
    }


def _mapping(value: Any) -> dict:
    """Convert dict-like/Pydantic trace objects without importing backend."""
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        return dumped if isinstance(dumped, dict) else {}
    return {}


def _trace_for_question(traces: Any, question_id: str) -> dict:
    if isinstance(traces, Mapping):
        trace = traces.get(question_id)
        # Some callers persist the full answer result under the question ID.
        trace_mapping = _mapping(trace)
        nested = trace_mapping.get("trace")
        return _mapping(nested) if nested is not None else trace_mapping
    return {}


def _trace_stages(trace: dict) -> list[dict]:
    stages = trace.get("stages", [])
    if isinstance(stages, Mapping):
        normalized = []
        for name, value in stages.items():
            stage = _mapping(value)
            normalized.append({"name": stage.get("name", name), **stage})
        return normalized
    if not isinstance(stages, (list, tuple)):
        return []
    normalized = []
    for index, value in enumerate(stages):
        stage = _mapping(value)
        if not stage:
            continue
        normalized.append({"name": stage.get("name", f"stage_{index}"), **stage})
    return normalized


def _trace_chunks(trace: dict) -> dict:
    chunks = trace.get("chunks", {})
    if not isinstance(chunks, Mapping):
        return {}
    return {str(key): _mapping(value) for key, value in chunks.items()}


def _trace_candidate_result(candidate: Any, chunks: dict) -> dict:
    """Turn a RetrievalTrace candidate into the metrics retrieval contract."""
    candidate_mapping = _mapping(candidate)
    chunk_id = candidate_mapping.get("chunk_id")
    chunk = chunks.get(str(chunk_id), {}) if chunk_id is not None else {}
    metadata = {}
    chunk_metadata = chunk.get("metadata")
    candidate_metadata = candidate_mapping.get("metadata")
    if isinstance(chunk_metadata, dict):
        metadata.update(chunk_metadata)
    if isinstance(candidate_metadata, dict):
        metadata.update(candidate_metadata)
    for key in (
        "document_name",
        "unit_id",
        "unit_index",
        "start_char",
        "end_char",
        "locator_kind",
        "location_label",
        "page_number",
    ):
        if key not in metadata:
            if key in candidate_mapping:
                metadata[key] = candidate_mapping[key]
            elif key in chunk:
                metadata[key] = chunk[key]
    content = candidate_mapping.get("content")
    if content is None:
        content = chunk.get("content", "")
    result = {
        "chunk_id": chunk_id,
        "content": content or "",
        "metadata": metadata,
    }
    for key in ("distance", "reranker_score", "rrf_score", "bm25_score"):
        if key in candidate_mapping:
            result[key] = candidate_mapping[key]
    return result


def _trace_stage_retrieval(
    stage: dict, trace: dict, *, eligible_only: bool = False
) -> list[dict]:
    candidates = stage.get("candidates", [])
    if not isinstance(candidates, (list, tuple)):
        return []
    chunks = _trace_chunks(trace)
    selected = []
    for candidate in candidates:
        candidate_mapping = _mapping(candidate)
        if eligible_only and candidate_mapping.get("eligible", True) is False:
            continue
        selected.append(_trace_candidate_result(candidate, chunks))
    return selected


TRACE_TIMING_KEYS = (
    "query_embedding",
    "dense_retrieval",
    "sparse_retrieval",
    "rrf",
    "rerank",
    "total",
)


def aggregate_trace_metrics(
    *, questions: list[dict], traces: Mapping[str, Any]
) -> dict:
    """聚合可选 RetrievalTrace 的阶段文档指标和耗时。

    Trace 只用于离线诊断：每个 stage 的候选会被转换为 metrics 的纯函数
    retrieval contract，不会触发检索、数据库或 LLM。
    """
    stage_rows: dict[str, list[dict]] = {}
    timing_values = {key: [] for key in TRACE_TIMING_KEYS}
    for question in questions:
        trace = _trace_for_question(traces, question["id"])
        if not trace:
            continue
        timings = _mapping(trace.get("timings_ms"))
        for key in TRACE_TIMING_KEYS:
            value = timings.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                timing_values[key].append(float(value))
        if not question.get("answerable", False):
            continue
        for stage in _trace_stages(trace):
            name = str(stage.get("name", "stage"))
            variants = [(name, False)]
            # Dense trace candidates are pre-threshold raw candidates.  Keep
            # the historical ``dense`` alias, and expose both the raw and
            # threshold-eligible views so a raw hit cannot hide filtering loss.
            if name.lower() == "dense":
                variants = [("dense", False), ("dense_raw", False), ("dense_eligible", True)]
            for variant_name, eligible_only in variants:
                retrieval = _trace_stage_retrieval(
                    stage,
                    trace,
                    eligible_only=eligible_only,
                )
                metrics = document_metrics(question, retrieval)
                stage_rows.setdefault(variant_name, []).append(
                    {
                        "id": question["id"],
                        "document_hit_at_k": metrics["document_hit_at_k"],
                        "document_recall_at_k": metrics["document_recall_at_k"],
                        "document_mrr": metrics["document_mrr"],
                        "all_gold_docs_recalled": metrics["all_gold_docs_recalled"],
                    }
                )

    stages = {}
    for name, rows in stage_rows.items():
        stages[name] = {
            "question_count": len(rows),
            "document_hit_rate": _mean(
                [1.0 if row["document_hit_at_k"] else 0.0 for row in rows]
            ),
            "document_recall": _mean(
                [row["document_recall_at_k"] for row in rows]
            ),
            "document_mrr": _mean([row["document_mrr"] for row in rows]),
            "all_gold_docs_recalled_rate": _mean(
                [1.0 if row["all_gold_docs_recalled"] else 0.0 for row in rows]
            ),
            "per_question": rows,
        }
        # Familiar aliases make the trace block easy to compare with the
        # candidate aggregate while keeping corrected names explicit.
        stages[name]["hit_rate"] = stages[name]["document_hit_rate"]
        stages[name]["recall"] = stages[name]["document_recall"]
        stages[name]["mrr"] = stages[name]["document_mrr"]

    return {
        "stages": stages,
        "timings_ms": {key: _latency(values) for key, values in timing_values.items()},
    }


def _score_distribution(values: list[float]) -> dict:
    """分布统计；字段不存在时返回 null + unavailable，而非伪造 0。"""
    if not values:
        return {
            "count": 0,
            "mean": None,
            "p50": None,
            "p95": None,
            "available": False,
            "status": "unavailable",
        }
    return {
        "count": len(values),
        "mean": round(sum(values) / len(values), 4),
        "p50": round(_percentile(values, 50), 4),
        "p95": round(_percentile(values, 95), 4),
        "available": True,
        "status": "available",
    }


def _top1_score_diagnostics(rows: list[dict]) -> dict:
    """汇总 final retrieval 的 Top1 分布和可用分数的组间差异。"""
    groups = {
        "answerable": [row for row in rows if row.get("answerable")],
        "unanswerable": [row for row in rows if not row.get("answerable")],
    }
    fields = ("distance", "rrf_score", "reranker_score")
    distributions = {}
    separation = {}
    for group_name, group_rows in groups.items():
        distributions[group_name] = {}
        for field in fields:
            values = [
                row.get(f"top1_{field}")
                for row in group_rows
                if isinstance(row.get(f"top1_{field}"), (int, float))
                and not isinstance(row.get(f"top1_{field}"), bool)
            ]
            distributions[group_name][field] = _score_distribution(values)
    for field in fields:
        answerable_stats = distributions["answerable"][field]
        unanswerable_stats = distributions["unanswerable"][field]
        available = answerable_stats["available"] and unanswerable_stats["available"]
        separation[field] = {
            "available": available,
            "status": "available" if available else "unavailable",
            "answerable_mean": answerable_stats["mean"],
            "unanswerable_mean": unanswerable_stats["mean"],
            "mean_delta_unanswerable_minus_answerable": (
                round(
                    unanswerable_stats["mean"] - answerable_stats["mean"],
                    4,
                )
                if available
                else None
            ),
        }
    return {
        "top1_score_distributions": distributions,
        "score_separation": separation,
        "unanswerable_top1_distance": distributions["unanswerable"]["distance"],
        "unanswerable_top1_rrf_score": distributions["unanswerable"]["rrf_score"],
        "unanswerable_top1_reranker_score": distributions["unanswerable"]["reranker_score"],
        "unanswerable_top1_distance_distribution": distributions["unanswerable"][
            "distance"
        ],
        "unanswerable_top1_rrf_score_distribution": distributions["unanswerable"][
            "rrf_score"
        ],
        "unanswerable_top1_reranker_score_distribution": distributions[
            "unanswerable"
        ]["reranker_score"],
    }


def aggregate_rows(rows: list[dict]) -> dict:
    answerable = [row for row in rows if row["answerable"]]
    unanswerable = [row for row in rows if not row["answerable"]]
    result = {"question_count": len(rows)}
    if answerable:
        result.update(
            {
                "hit_rate": _mean(
                    [1.0 if row["hit"] else 0.0 for row in answerable]
                ),
                "recall": _mean([row["recall"] for row in answerable]),
                "mrr": _mean(
                    [row["reciprocal_rank"] for row in answerable]
                ),
                "document_hit_rate": _mean(
                    [
                        1.0
                        if _first_present(
                            row,
                            "document_hit_at_k",
                            "document_hit",
                            "hit",
                            default=False,
                        )
                        else 0.0
                        for row in answerable
                    ]
                ),
                "document_recall": _mean(
                    [
                        _first_present(
                            row,
                            "document_recall_at_k",
                            "document_recall",
                            "recall",
                            default=None,
                        )
                        for row in answerable
                    ]
                ),
                "document_mrr": _mean(
                    [
                        _first_present(
                            row,
                            "document_mrr",
                            "document_reciprocal_rank",
                            "reciprocal_rank",
                            default=None,
                        )
                        for row in answerable
                    ]
                ),
                "all_gold_docs_recalled_rate": _mean(
                    [1.0 if row.get("all_gold_docs_recalled") else 0.0 for row in answerable]
                ),
            }
        )
        multi_document = [row for row in answerable if row.get("multi_document")]
        result["multi_document_count"] = len(multi_document)
        result["multi_document_complete_recall_rate"] = _mean(
            [
                1.0
                if row.get(
                    "multi_document_complete_recall",
                    row.get("all_gold_docs_recalled"),
                )
                else 0.0
                for row in multi_document
            ]
        )
        result["exact_evidence_containment_rate"] = _mean(
            [1.0 if row.get("hit") else 0.0 for row in answerable]
        )
        result["evidence_coverage"] = _mean(
            [row.get("evidence_coverage") for row in answerable]
        )
        result["answerable_empty_rate"] = _mean(
            [1.0 if not row.get("result_count", 0) else 0.0 for row in answerable]
        )
        result["false_refusal_gate_rate"] = result["answerable_empty_rate"]
    if unanswerable:
        result.update(aggregate_unanswerable_gates(unanswerable))
    result.update(_top1_score_diagnostics(rows))
    return result


def build_candidate_result(
    *,
    candidate_k: int,
    questions: list[dict],
    retrievals: dict[str, list[dict]],
    timings_s: list[float],
    traces: dict[str, Any] | None = None,
) -> dict:
    """计算单个 candidate_k 的确定性检索指标。

    ``traces`` 为可选的 ``question_id -> RetrievalTrace`` 映射；传入时额外
    汇总各 stage 文档级指标和 timings_ms，旧调用仍只得到候选指标。
    """
    rows = []
    for question in questions:
        retrieval = retrievals[question["id"]]
        row = {
            "id": question["id"],
            "category": question["category"],
            "split": question.get("split", "unspecified"),
            "answerable": question["answerable"],
            "result_count": len(retrieval),
            "top1_distance": retrieval[0].get("distance") if retrieval else None,
            "top1_reranker_score": (
                retrieval[0].get("reranker_score") if retrieval else None
            ),
            "top1_rrf_score": retrieval[0].get("rrf_score") if retrieval else None,
        }
        if question["answerable"]:
            exact = exact_evidence_metrics(question, retrieval)
            document = document_metrics(question, retrieval)
            coverage = evidence_coverage_details(question, retrieval)
            row.update(
                {
                    **exact,
                    "evidence_total": exact["exact_evidence_total"],
                    "evidence_matched": exact["exact_evidence_matched"],
                    "hit": exact["exact_evidence_hit"],
                    "recall": exact["exact_evidence_recall"],
                    "first_match_rank": exact["exact_evidence_first_match_rank"],
                    "reciprocal_rank": exact["exact_evidence_reciprocal_rank"],
                    "exact_evidence_containment": exact["exact_evidence_hit"],
                    "exact_evidence_containment_rate": exact[
                        "exact_evidence_recall"
                    ],
                    **document,
                    "document_hit": document["document_hit_at_k"],
                    "document_recall": document["document_recall_at_k"],
                    "document_reciprocal_rank": document["document_mrr"],
                    "evidence_coverage": coverage["coverage"],
                    "evidence_coverage_values": coverage["values"],
                    "evidence_coverage_available": coverage["available"],
                    "evidence_coverage_total": coverage["total"],
                    "evidence_coverage_missing_offsets": coverage[
                        "missing_offsets"
                    ],
                    "evidence_coverage_all_available": coverage["all_available"],
                    "evidence_coverage_fallback": (
                        "exact_evidence_containment"
                        if coverage["coverage"] is None
                        else None
                    ),
                }
            )
        else:
            row["llm_entry"] = bool(retrieval)
            row["refusal_gate"] = not bool(retrieval)
        rows.append(row)

    aggregate = aggregate_rows(rows)
    by_category = {
        category: aggregate_rows(
            [row for row in rows if row["category"] == category]
        )
        for category in sorted({row["category"] for row in rows})
    }
    by_split = {
        split: aggregate_rows([row for row in rows if row["split"] == split])
        for split in sorted({row["split"] for row in rows})
    }
    result = {
        "candidate_k": candidate_k,
        **aggregate,
        "latency_s": _latency(timings_s),
        "by_category": by_category,
        "by_split": by_split,
        "retrieval_failure_ids": [
            row["id"]
            for row in rows
            if row["answerable"] and not row["hit"]
        ],
        "unanswerable_llm_entry_ids": [
            row["id"]
            for row in rows
            if not row["answerable"] and row["llm_entry"]
        ],
        "per_question": rows,
    }
    if traces:
        result["trace_metrics"] = aggregate_trace_metrics(
            questions=questions,
            traces=traces,
        )
        # Top-level aliases ease consumption by sweep/report callers while the
        # nested block remains the canonical trace payload.
        result["trace_stages"] = result["trace_metrics"]["stages"]
        result["trace_timings_ms"] = result["trace_metrics"]["timings_ms"]
    return result


def _recommend_candidate(candidates: list[dict]) -> int:
    if not candidates:
        raise ValueError("至少需要一个 Reranker 候选配置")

    def quality(row: dict) -> tuple[float, float, float, float, float]:
        """Corrected quality tuple with fallback for historical candidates."""
        document_hit = _first_present(
            row,
            "document_hit_rate",
            "hit_rate",
            default=0.0,
        )
        document_recall = _first_present(
            row,
            "document_recall",
            "document_recall_rate",
            "recall",
            default=0.0,
        )
        document_mrr = _first_present(
            row,
            "document_mrr",
            "mrr",
            default=0.0,
        )
        all_gold = _first_present(
            row,
            "all_gold_docs_recalled_rate",
            "multi_document_complete_recall_rate",
            # Historical reports did not have this field.  Recall is the
            # least misleading fallback until a corrected candidate exists.
            "document_recall",
            "recall",
            default=0.0,
        )
        coverage = _first_present(
            row,
            "evidence_coverage",
            "exact_evidence_containment_rate",
            "recall",
            default=0.0,
        )
        return tuple(
            float(value) if isinstance(value, (int, float)) else 0.0
            for value in (
                document_hit,
                document_recall,
                document_mrr,
                all_gold,
                coverage,
            )
        )

    best_quality = max(quality(row) for row in candidates)
    plateau = [
        row
        for row in candidates
        if quality(row) == best_quality
    ]
    selected = min(
        plateau,
        key=lambda row: (row["latency_s"]["p95"], row["candidate_k"]),
    )
    return selected["candidate_k"]


def build_retrieval_sweep(
    *,
    label: str,
    config: dict,
    ingest: dict,
    candidates: list[dict],
    dataset_version: int | str,
) -> dict:
    return {
        "label": label,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "config": {**config, "dataset_version": dataset_version},
        "ingest": ingest,
        "recommended_candidate_k": _recommend_candidate(candidates),
        "candidates": candidates,
    }


def _percent(value) -> str:
    return "—" if value is None else f"{value:.2%}"


def render_retrieval_sweep(payload: dict) -> str:
    lines = [
        f"# Reranker 候选数扫描 — {payload['label']}",
        "",
        f"- 模型：`{payload['config']['reranker_model']}`",
        f"- 推荐候选数：`{payload['recommended_candidate_k']}`",
        f"- 入库：{payload['ingest']['total_chunks']} chunks，"
        f"{payload['ingest']['ingest_s']}s",
        "",
        "> 选择规则：先最大化 Hit Rate、Recall、MRR；质量相同则选择 P95 延迟更低、"
        "候选数更小的配置。",
        "",
        "| 候选数 | Hit Rate | Recall | MRR | 不可回答题进入 LLM | mean(s) | p50(s) | p95(s) |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["candidates"]:
        latency = row["latency_s"]
        lines.append(
            f"| {row['candidate_k']} | {_percent(row.get('hit_rate'))} "
            f"| {_percent(row.get('recall'))} | {_percent(row.get('mrr'))} "
            f"| {_percent(row.get('unanswerable_llm_entry_rate'))} "
            f"| {latency['mean']} | {latency['p50']} | {latency['p95']} |"
        )

    lines += ["", "## 逐候选失败明细", ""]
    for row in payload["candidates"]:
        failures = "、".join(row["retrieval_failure_ids"]) or "无"
        entries = "、".join(row["unanswerable_llm_entry_ids"]) or "无"
        lines.append(
            f"- `{row['candidate_k']}`：检索失败题：{failures}；"
            f"不可回答题进入 LLM：{entries}"
        )

    lines += [
        "",
        "## 修正后的文档级与证据诊断指标",
        "",
        "| 候选数 | document Hit@K | document Recall@K | document MRR | all-gold-docs recalled | evidence coverage | exact evidence containment(诊断) |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["candidates"]:
        lines.append(
            f"| {row['candidate_k']} | {_percent(row.get('document_hit_rate'))} "
            f"| {_percent(row.get('document_recall'))} "
            f"| {_percent(row.get('document_mrr'))} "
            f"| {_percent(row.get('all_gold_docs_recalled_rate'))} "
            f"| {_percent(row.get('evidence_coverage'))} "
            f"| {_percent(row.get('exact_evidence_containment_rate'))} |"
        )

    lines += [
        "",
        "## 数据划分结果",
        "",
        "| 候选数 | split | 题数 | document Hit@K | document Recall@K | document MRR | all-gold-docs recalled | 拒答 gate 准确率 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["candidates"]:
        for split, metrics in (row.get("by_split") or {}).items():
            lines.append(
                f"| {row['candidate_k']} | {split} | {metrics['question_count']} "
                f"| {_percent(metrics.get('document_hit_rate'))} "
                f"| {_percent(metrics.get('document_recall'))} "
                f"| {_percent(metrics.get('document_mrr'))} "
                f"| {_percent(metrics.get('all_gold_docs_recalled_rate'))} "
                f"| {_percent(metrics.get('unanswerable_refusal_gate_rate'))} |"
            )

    lines += [
        "",
        "## 不可回答题门控与 Top1 分数诊断",
        "",
        "| 候选数 | 不可回答题进入 LLM | 拒答 gate 准确率 | 可回答题误拒答 gate | unanswerable distance(mean/p50/p95) | RRF(mean/p50/p95) | Reranker(mean/p50/p95) |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]

    def _score_summary(row: dict, key: str) -> str:
        stats = row.get(key) or {}
        if not stats.get("available"):
            return "不可用"
        return f"{stats['mean']}/{stats['p50']}/{stats['p95']}"

    for row in payload["candidates"]:
        lines.append(
            f"| {row['candidate_k']} | {_percent(row.get('unanswerable_llm_entry_rate'))} "
            f"| {_percent(row.get('unanswerable_refusal_gate_rate'))} "
            f"| {_percent(row.get('false_refusal_gate_rate'))} "
            f"| {_score_summary(row, 'unanswerable_top1_distance_distribution')} "
            f"| {_score_summary(row, 'unanswerable_top1_rrf_score_distribution')} "
            f"| {_score_summary(row, 'unanswerable_top1_reranker_score_distribution')} |"
        )

    lines += [
        "",
        "分数分离度（unanswerable mean − answerable mean；缺任一组或字段时为不可用）：",
        "",
    ]
    for row in payload["candidates"]:
        separation = row.get("score_separation") or {}
        values = []
        for key in ("distance", "rrf_score", "reranker_score"):
            item = separation.get(key) or {}
            value = item.get("mean_delta_unanswerable_minus_answerable")
            values.append(f"{key}={value}" if value is not None else f"{key}=不可用")
        lines.append(f"- `{row['candidate_k']}`：" + "；".join(values))
    return "\n".join(lines)
