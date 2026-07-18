"""检索距离阈值的离线扫描、推荐与报告生成。"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from eval.metrics import match_evidence


def _round(value: float) -> float:
    return round(value, 4)


def _evaluate_threshold(questions: list[dict], answers: dict, threshold: float) -> dict:
    answerable = [q for q in questions if q["answerable"]]
    unanswerable = [q for q in questions if not q["answerable"]]
    answerable_empty_ids = []
    unanswerable_llm_entry_ids = []
    hits = []
    recalls = []
    reciprocal_ranks = []

    for question in questions:
        retrieval = [
            row
            for row in answers[question["id"]]["retrieval"]
            if row["distance"] <= threshold
        ]
        if question["answerable"]:
            if not retrieval:
                answerable_empty_ids.append(question["id"])
            ranks = [
                match_evidence(evidence, retrieval)
                for evidence in question["evidence"]
            ]
            matched = [rank for rank in ranks if rank is not None]
            hits.append(1.0 if matched else 0.0)
            recalls.append(
                len(matched) / len(question["evidence"])
                if question["evidence"]
                else 0.0
            )
            reciprocal_ranks.append(1.0 / (min(matched) + 1) if matched else 0.0)
        elif retrieval:
            unanswerable_llm_entry_ids.append(question["id"])

    answerable_count = len(answerable)
    unanswerable_count = len(unanswerable)
    return {
        "threshold": round(float(threshold), 4),
        "hit_rate": _round(sum(hits) / answerable_count) if answerable_count else None,
        "recall": _round(sum(recalls) / answerable_count) if answerable_count else None,
        "mrr": _round(sum(reciprocal_ranks) / answerable_count) if answerable_count else None,
        "threshold_false_refusal_rate": _round(
            len(answerable_empty_ids) / answerable_count
        ) if answerable_count else None,
        "answerable_empty_ids": answerable_empty_ids,
        "unanswerable_llm_entry_rate": _round(
            len(unanswerable_llm_entry_ids) / unanswerable_count
        ) if unanswerable_count else None,
        "unanswerable_llm_entry_ids": unanswerable_llm_entry_ids,
    }


def _recommend_threshold(candidates: list[dict]) -> float:
    """选择质量/门控最优平台的中位阈值，避免贴近平台边界。"""
    if not candidates:
        raise ValueError("至少需要一个候选阈值")

    def objective(row):
        return (
            -row["threshold_false_refusal_rate"],
            row["hit_rate"],
            row["recall"],
            row["mrr"],
            -row["unanswerable_llm_entry_rate"],
        )

    best_objective = max(objective(row) for row in candidates)
    plateau = sorted(
        row["threshold"]
        for row in candidates
        if objective(row) == best_objective
    )
    return plateau[(len(plateau) - 1) // 2]


def build_threshold_sweep(
    *,
    label: str,
    questions: list[dict],
    answers: dict,
    thresholds: Iterable[float],
    source: str,
) -> dict:
    """基于一次真实检索结果，离线重放多个距离阈值。"""
    normalized = sorted({round(float(value), 4) for value in thresholds})
    candidates = [
        _evaluate_threshold(questions, answers, threshold)
        for threshold in normalized
    ]
    return {
        "label": label,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "source": source,
        "question_count": len(questions),
        "recommended_threshold": _recommend_threshold(candidates),
        "candidates": candidates,
    }


def _percent(value) -> str:
    return "—" if value is None else f"{value:.2%}"


def render_threshold_report(payload: dict) -> str:
    """把阈值扫描结果渲染为便于审查的 Markdown。"""
    lines = [
        f"# 检索阈值扫描 — {payload['label']}",
        "",
        f"- 数据来源：`{payload['source']}`",
        f"- 题目数：{payload['question_count']}",
        f"- 推荐阈值：`{payload['recommended_threshold']:.2f}`",
        "",
        "> 推荐规则：先避免阈值导致可回答题空检索，再保持 Hit/Recall/MRR，"
        "随后尽量减少不可回答题进入 LLM；若形成等价平台，选择平台中点。",
        "",
        "| 阈值 | 检索命中率 | 召回率 | MRR | 阈值诱发误拒答率 | 不可回答题进入 LLM 比例 |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["candidates"]:
        lines.append(
            f"| {row['threshold']:.2f} | {_percent(row['hit_rate'])} "
            f"| {_percent(row['recall'])} | {_percent(row['mrr'])} "
            f"| {_percent(row['threshold_false_refusal_rate'])} "
            f"| {_percent(row['unanswerable_llm_entry_rate'])} |"
        )

    lines += ["", "## 逐阈值门控明细", ""]
    for row in payload["candidates"]:
        empty = "、".join(row["answerable_empty_ids"]) or "无"
        entered = "、".join(row["unanswerable_llm_entry_ids"]) or "无"
        lines.append(
            f"- `{row['threshold']:.2f}`：可回答题空检索={empty}；"
            f"不可回答题进入 LLM={entered}"
        )
    return "\n".join(lines)
