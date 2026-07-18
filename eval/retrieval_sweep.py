"""无 LLM 的检索候选实验指标、选择规则与 Markdown 报告。"""

from __future__ import annotations

from datetime import datetime

from eval.metrics import match_evidence


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


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


def _aggregate_rows(rows: list[dict]) -> dict:
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
            }
        )
    if unanswerable:
        result["unanswerable_llm_entry_rate"] = _mean(
            [1.0 if row["llm_entry"] else 0.0 for row in unanswerable]
        )
    return result


def build_candidate_result(
    *,
    candidate_k: int,
    questions: list[dict],
    retrievals: dict[str, list[dict]],
    timings_s: list[float],
) -> dict:
    """计算单个 candidate_k 的确定性检索指标。"""
    rows = []
    for question in questions:
        retrieval = retrievals[question["id"]]
        row = {
            "id": question["id"],
            "category": question["category"],
            "answerable": question["answerable"],
            "result_count": len(retrieval),
            "top1_distance": retrieval[0]["distance"] if retrieval else None,
            "top1_reranker_score": (
                retrieval[0].get("reranker_score") if retrieval else None
            ),
        }
        if question["answerable"]:
            ranks = [
                match_evidence(evidence, retrieval)
                for evidence in question["evidence"]
            ]
            matched = [rank for rank in ranks if rank is not None]
            row.update(
                {
                    "evidence_total": len(question["evidence"]),
                    "evidence_matched": len(matched),
                    "hit": bool(matched),
                    "recall": (
                        len(matched) / len(question["evidence"])
                        if question["evidence"]
                        else 0.0
                    ),
                    "first_match_rank": min(matched) + 1 if matched else None,
                    "reciprocal_rank": (
                        1.0 / (min(matched) + 1) if matched else 0.0
                    ),
                }
            )
        else:
            row["llm_entry"] = bool(retrieval)
        rows.append(row)

    aggregate = _aggregate_rows(rows)
    by_category = {
        category: _aggregate_rows(
            [row for row in rows if row["category"] == category]
        )
        for category in sorted({row["category"] for row in rows})
    }
    return {
        "candidate_k": candidate_k,
        **aggregate,
        "latency_s": _latency(timings_s),
        "by_category": by_category,
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


def _recommend_candidate(candidates: list[dict]) -> int:
    if not candidates:
        raise ValueError("至少需要一个 Reranker 候选配置")
    best_quality = max(
        (row["hit_rate"], row["recall"], row["mrr"])
        for row in candidates
    )
    plateau = [
        row
        for row in candidates
        if (row["hit_rate"], row["recall"], row["mrr"]) == best_quality
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
    dataset_version: int,
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
    return "\n".join(lines)
