"""P2-1 Dense/BM25/Hybrid/Reranker 离线消融聚合与报告。"""

from __future__ import annotations

from datetime import datetime

from eval.retrieval_sweep import aggregate_rows, build_candidate_result


ABLATION_CONFIGS = [
    ("dense", "dense", False),
    ("bm25", "bm25", False),
    ("hybrid", "hybrid", False),
    ("hybrid_reranker", "hybrid", True),
    ("dense_reranker", "dense", True),
]

MODE_LABELS = {
    "dense": "Dense",
    "bm25": "BM25",
    "hybrid": "Hybrid",
    "hybrid_reranker": "Hybrid + Reranker",
    "dense_reranker": "Dense + Reranker",
}

_ORIGINAL_IDS = {f"q{index:02d}" for index in range(1, 22)}


def build_mode_result(
    *,
    name: str,
    questions: list[dict],
    retrievals: dict[str, list[dict]],
    timings_s: list[float],
) -> dict:
    """复用确定性检索指标，并额外拆分原 21 题和 lexical 子集。"""
    result = build_candidate_result(
        candidate_k=0,
        questions=questions,
        retrievals=retrievals,
        timings_s=timings_s,
    )
    result.pop("candidate_k", None)
    result["name"] = name
    result["original_21"] = aggregate_rows(
        [row for row in result["per_question"] if row["id"] in _ORIGINAL_IDS]
    )
    result["lexical"] = aggregate_rows(
        [
            row
            for row in result["per_question"]
            if row["category"] == "lexical"
        ]
    )
    return result


def _row_quality(row: dict) -> tuple:
    if row["answerable"]:
        return (
            bool(row.get("hit")),
            row.get("recall", 0.0),
            row.get("reciprocal_rank", 0.0),
        )
    return (not row.get("llm_entry", False),)


def _deployment_comparison(results: list[dict]) -> dict:
    by_name = {row["name"]: row for row in results}
    baseline = by_name["dense_reranker"]
    hybrid = by_name["hybrid_reranker"]
    baseline_rows = {row["id"]: row for row in baseline["per_question"]}
    hybrid_rows = {row["id"]: row for row in hybrid["per_question"]}
    improved, regressed = [], []
    for question_id in sorted(baseline_rows):
        before = _row_quality(baseline_rows[question_id])
        after = _row_quality(hybrid_rows[question_id])
        if after > before:
            improved.append(question_id)
        elif after < before:
            regressed.append(question_id)

    deltas = {
        f"{metric}_delta": round(
            hybrid.get(metric, 0.0) - baseline.get(metric, 0.0), 4
        )
        for metric in ("hit_rate", "recall", "mrr")
    }
    return {
        "baseline": "dense_reranker",
        "candidate": "hybrid_reranker",
        **deltas,
        "improved_ids": improved,
        "regressed_ids": regressed,
        "offline_gate_passed": (
            any(value > 0 for value in deltas.values()) and not regressed
        ),
    }


def build_hybrid_ablation(
    *,
    label: str,
    config: dict,
    ingest: dict,
    results: list[dict],
    dataset_version: int | str,
) -> dict:
    """组装机器可读消融报告。"""
    return {
        "label": label,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "config": {**config, "dataset_version": dataset_version},
        "ingest": ingest,
        "results": results,
        "deployment_comparison": _deployment_comparison(results),
    }


def _percent(value) -> str:
    return "—" if value is None else f"{value:.2%}"


def render_hybrid_ablation(payload: dict) -> str:
    """渲染五组指标、同数据集部署对照和逐题变化。"""
    comparison = payload["deployment_comparison"]
    question_count = max(
        (row.get("question_count", 0) for row in payload["results"]),
        default=0,
    )
    dataset_version = payload["config"]["dataset_version"]
    dataset_label = (
        f"v{dataset_version}"
        if isinstance(dataset_version, int)
        else str(dataset_version)
    )
    lines = [
        f"# P2-1 混合检索消融 — {payload['label']}",
        "",
        f"- Dataset：{dataset_label}（{question_count} 题）",
        f"- 入库：{payload['ingest']['total_chunks']} chunks，"
        f"{payload['ingest']['ingest_s']}s",
        "- 部署对照：Dense + Reranker → Hybrid + Reranker",
        "",
        "## 指标口径",
        "",
        "| 指标 | 指标含义 |",
        "|---|---|",
        "| Hit Rate | 可回答题中，Top 5 至少命中一条 gold 证据的题目占比 |",
        "| Recall | 每道可回答题命中的 gold 证据比例，再对题目取平均 |",
        "| MRR | 每道可回答题首条 gold 证据排名的倒数，再取平均 |",
        "| 原21题 Recall | 仅在 v1 原有 q01-q21 上计算的 Recall，用于守住历史能力 |",
        "| Lexical Recall | 仅在 v2 新增精确术语题上计算的 Recall |",
        "| 不可回答题进入 LLM | 无 gold 证据题仍返回检索结果的比例，越低越好 |",
        "| P95(s) | 本次数据集单题检索耗时的第 95 百分位，单位为秒 |",
        "",
        "## 消融结果",
        "",
        "| 模式 | Hit Rate | Recall | MRR | 原21题 Recall | Lexical Recall | 不可回答题进入 LLM | P95(s) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["results"]:
        lines.append(
            f"| {MODE_LABELS[row['name']]} | {_percent(row.get('hit_rate'))} "
            f"| {_percent(row.get('recall'))} | {_percent(row.get('mrr'))} "
            f"| {_percent(row['original_21'].get('recall'))} "
            f"| {_percent(row['lexical'].get('recall'))} "
            f"| {_percent(row.get('unanswerable_llm_entry_rate'))} "
            f"| {row['latency_s']['p95']} |"
        )

    improved = "、".join(comparison["improved_ids"]) or "无"
    regressed = "、".join(comparison["regressed_ids"]) or "无"
    lines += [
        "",
        "## 部署门槛（同 dataset v2）",
        "",
        f"- Hit Rate Δ：{comparison['hit_rate_delta']:+.2%}",
        f"- Recall Δ：{comparison['recall_delta']:+.2%}",
        f"- MRR Δ：{comparison['mrr_delta']:+.2%}",
        f"- 改善题：{improved}",
        f"- 回归题：{regressed}",
        f"- 离线门槛：{'通过' if comparison['offline_gate_passed'] else '未通过'}",
        "",
        "## 各模式失败题",
        "",
    ]
    for row in payload["results"]:
        failures = "、".join(row["retrieval_failure_ids"]) or "无"
        entries = "、".join(row["unanswerable_llm_entry_ids"]) or "无"
        lines.append(
            f"- {MODE_LABELS[row['name']]}：检索失败 {failures}；"
            f"不可回答题进入 LLM {entries}"
        )
    return "\n".join(lines)
