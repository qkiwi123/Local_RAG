"""
报告生成 — report.json（机器可读）/ report.md（汇总+失败案例）/
review.md（人工评审文档）/ compare（两个 run 的差值对比）。
"""

import json
from datetime import datetime
from pathlib import Path
from typing import List

# 汇总表中各指标的展示顺序、中文名与含义（compare 也复用）
METRIC_LABELS = [
    ("hit_rate", "检索命中率 hit_rate", "检索 top-k 中至少命中 1 条 gold 证据的可回答题占比"),
    ("recall", "检索召回率 recall", "每道可回答题命中的 gold 证据占该题全部 gold 证据的比例，再取平均"),
    ("mrr", "MRR", "每道可回答题首个命中 chunk 的排名倒数，再取平均"),
    ("keypoint_coverage", "关键点覆盖率", "回答命中的关键点组占全部关键点组的比例，再取平均"),
    ("strict_accuracy", "严格准确率", "全部关键点命中且未拒答的可回答题占比"),
    ("citation_precision", "引用精确率", "有效引用文档中属于 gold 文档的比例，再取平均"),
    ("citation_recall", "引用召回率", "被有效引用的 gold 文档占全部 gold 文档的比例，再取平均"),
    ("citation_emission_rate", "引用输出率", "包含至少一个引用标记的可回答题占比"),
    ("refusal_accuracy", "拒答准确率", "不可回答题中被正确拒答的题目占比"),
    ("false_refusal_rate", "误拒答率(↓)", "可回答题中被判定为拒答的题目占比，越低越好"),
    ("unanswerable_citation_fp", "拒答题伪引用数(↓)", "不可回答题中出现引用标记的题目数量，越低越好"),
]


def _fmt(v):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.2%}" if 0 <= v <= 1 else f"{v:.3f}"
    return str(v)


def _blockquote(text: str) -> str:
    """Render Markdown blockquotes without trailing spaces on blank lines."""
    return "\n".join(f"> {line}" if line else ">" for line in text.split("\n"))


def _format_location(metadata: dict) -> str:
    """Format current page metadata and future structured source locators."""
    location_label = metadata.get("location_label")
    if location_label:
        return str(location_label)
    slide_number = metadata.get("slide_number")
    if slide_number is not None:
        return f"第 {slide_number} 张幻灯片"
    page_number = metadata.get("page_number")
    if page_number is not None:
        return f"第{page_number}页"
    return "位置未知"


def _format_evidence_location(evidence: dict) -> str:
    locator = evidence.get("locator") or {}
    if locator.get("location_label"):
        return str(locator["location_label"])
    page_hint = evidence.get("page_hint")
    return f"第{page_hint}页" if page_hint is not None else "位置未知"


def _format_retrieval_debug(row: dict) -> str:
    """格式化允许缺少 Dense distance 的混合检索调试信息。"""
    metadata = row["metadata"]
    distance = row.get("distance")
    scores = [f"dist={distance:.3f}" if distance is not None else "dist=—"]
    for key, label in (
        ("bm25_score", "bm25"),
        ("rrf_score", "rrf"),
        ("reranker_score", "reranker"),
    ):
        if row.get(key) is not None:
            scores.append(f"{label}={row[key]:.3f}")
    return (
        f"《{metadata.get('document_name')}》{_format_location(metadata)}"
        f"({', '.join(scores)})"
    )


def write_report_json(run_dir: Path, payload: dict):
    with open(run_dir / "report.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def write_report_md(run_dir: Path, payload: dict, questions: List[dict], answers: dict):
    """report.md：配置 + 汇总表 + 分类别表 + 失败案例明细。"""
    agg = payload["aggregate"]
    lines = [
        f"# 评估报告 — {payload['label']}",
        "",
        f"- 时间：{payload['timestamp']}",
        f"- 配置：`{json.dumps(payload['config'], ensure_ascii=False)}`",
        f"- 入库：{payload['ingest']['total_chunks']} chunks，"
        f"耗时 {payload['ingest']['ingest_s']}s",
        "",
        "> 注：LLM 回答类指标可能存在非确定性；在取得重复运行证据前不预设固定噪声阈值，"
        "实际波动需通过重复运行测量。检索类指标在模型、语料和配置完全固定时应保持确定。",
        "",
        "## 汇总指标",
        "",
        "| 指标 | 指标含义 | 值 |",
        "|---|---|---|",
    ]
    for key, label, meaning in METRIC_LABELS:
        if key in agg:
            lines.append(f"| {label} | {meaning} | {_fmt(agg[key])} |")
    lat = payload["latency"]
    lines += [
        "",
        "## 延迟（秒）",
        "",
        "| 阶段 | mean | p50 | p95 |",
        "|---|---|---|---|",
        f"| 检索 | {lat['retrieval']['mean']} | {lat['retrieval']['p50']} | {lat['retrieval']['p95']} |",
        f"| LLM | {lat['llm']['mean']} | {lat['llm']['p50']} | {lat['llm']['p95']} |",
        f"| 总计 | {lat['total']['mean']} | {lat['total']['p50']} | {lat['total']['p95']} |",
        "",
        "## 分类别指标",
        "",
        "| 类别 | 题数 | 命中率 | 召回率 | MRR | 关键点覆盖 | 严格准确 | 拒答准确 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for cat, c in payload["by_category"].items():
        lines.append(
            f"| {cat} | {c['question_count']} | {_fmt(c.get('hit_rate'))} "
            f"| {_fmt(c.get('recall'))} | {_fmt(c.get('mrr'))} "
            f"| {_fmt(c.get('keypoint_coverage'))} | {_fmt(c.get('strict_accuracy'))} "
            f"| {_fmt(c.get('refusal_accuracy'))} |"
        )

    # ── 失败案例明细：检索未命中 / 关键点不全 / 拒答错误 ──
    qmap = {q["id"]: q for q in questions}
    failures = []
    for row in payload["per_question"]:
        q = qmap[row["id"]]
        if row["answerable"]:
            if not row["hit"] or not row.get("strict_pass"):
                failures.append((row, q))
        elif not row.get("refusal_correct"):
            failures.append((row, q))

    lines += ["", f"## 失败案例（{len(failures)} 题）", ""]
    for row, q in failures:
        a = answers[row["id"]]
        retrieved = [_format_retrieval_debug(r) for r in a["retrieval"]]
        lines += [
            f"### {row['id']} [{row['category']}] {q['question']}",
            "",
        ]
        if row["answerable"]:
            gold = "、".join(
                f"《{ev['document_name']}》「{ev['snippet']}」" for ev in q["evidence"]
            )
            lines += [
                f"- 检索命中：{'✓' if row['hit'] else '✗'}"
                f"（{row['evidence_matched']}/{row['evidence_total']} 条证据，"
                f"首命中排名 {row['first_match_rank']}）",
                f"- 关键点：{row['keypoint_satisfied']}/{row['keypoint_total']}",
                f"- 期望证据：{gold}",
            ]
        else:
            lines.append("- 应拒答但未拒答")
        lines += [
            f"- 实际检索：{'；'.join(retrieved) if retrieved else '（空）'}",
            f"- 实际引用：{row['cited_docs'] if row['cited_docs'] else '（无标记）'}",
            f"- 回答节选：{a['answer'][:150]}……"
            if len(a["answer"]) > 150
            else f"- 回答：{a['answer']}",
            "",
        ]

    with open(run_dir / "report.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def write_review_md(run_dir: Path, payload: dict, questions: List[dict], answers: dict):
    """
    review.md：人工评审文档。

    每题列出 问题/系统回答/标准答案/证据原文/自动指标摘要，
    末尾附空白评分表，可整体粘贴给网页端 AI 或手工填写。
    """
    rowmap = {r["id"]: r for r in payload["per_question"]}
    lines = [
        f"# 人工评审文档 — {payload['label']}",
        "",
        f"运行时间：{payload['timestamp']}。",
        "请逐题对比「系统回答」与「标准答案/证据原文」，在文末评分表中填写判定"
        "（正确 / 部分正确 / 错误），也可将本文档整体粘贴给 AI 协助评审。",
        "",
        "---",
        "",
    ]
    for q in questions:
        row = rowmap[q["id"]]
        a = answers[q["id"]]
        lines += [
            f"## {q['id']} [{q['category']}] {q['question']}",
            "",
            "**系统回答：**",
            "",
            _blockquote(a["answer"]),
            "",
            f"**标准答案：** {q['reference_answer']}",
            "",
        ]
        if q["evidence"]:
            lines.append("**证据原文：**")
            lines.append("")
            for ev in q["evidence"]:
                lines.append(
                    f"- 《{ev['document_name']}》{_format_evidence_location(ev)}："
                    f"「{ev['snippet']}」"
                )
            lines.append("")
        auto = []
        if row["answerable"]:
            auto.append(f"检索命中 {'✓' if row['hit'] else '✗'}")
            auto.append(f"关键点 {row['keypoint_satisfied']}/{row['keypoint_total']}")
            auto.append(f"引用标记 {row['citation_count']} 个")
        else:
            auto.append(f"拒答 {'✓ 正确' if row.get('refusal_correct') else '✗ 未拒答'}")
        lines += [f"**自动指标：** {'；'.join(auto)}", "", "---", ""]

    lines += [
        "## 评分表",
        "",
        "| 题号 | 人工判定（正确/部分/错误） | 备注 |",
        "|---|---|---|",
    ]
    for q in questions:
        lines.append(f"| {q['id']} |  |  |")

    with open(run_dir / "review.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def render_compare(a: dict, b: dict) -> str:
    """对比两份 report.json：指标差值表 + 回归题清单。"""
    lines = [
        f"# 评估对比：{a['label']} → {b['label']}",
        "",
        f"| 指标 | {a['label']} | {b['label']} | Δ |",
        "|---|---|---|---|",
    ]
    lower_is_better = {"false_refusal_rate", "unanswerable_citation_fp"}
    for key, label, _ in METRIC_LABELS:
        va, vb = a["aggregate"].get(key), b["aggregate"].get(key)
        if va is None and vb is None:
            continue
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            d = vb - va
            better = (d < 0) if key in lower_is_better else (d > 0)
            arrow = "▲" if better else ("▼" if d != 0 else "＝")
            delta = f"{arrow} {d:+.2%}" if abs(d) <= 1 else f"{arrow} {d:+.3f}"
        else:
            delta = "—"
        lines.append(f"| {label} | {_fmt(va)} | {_fmt(vb)} | {delta} |")

    lines += [
        "",
        f"| 延迟(总计s) | {a['latency']['total']['mean']} "
        f"| {b['latency']['total']['mean']} "
        f"| {b['latency']['total']['mean'] - a['latency']['total']['mean']:+.3f} |",
        f"| chunk 数 | {a['ingest']['total_chunks']} "
        f"| {b['ingest']['total_chunks']} "
        f"| {b['ingest']['total_chunks'] - a['ingest']['total_chunks']:+d} |",
    ]

    # ── 回归题清单：A 通过而 B 失败（判断"优化"是否引入退化的关键产物） ──
    def passed(row):
        if row["answerable"]:
            return row["hit"] and row.get("strict_pass", False)
        return row.get("refusal_correct", False)

    rows_a = {r["id"]: r for r in a["per_question"]}
    rows_b = {r["id"]: r for r in b["per_question"]}
    regressions = [
        qid
        for qid in rows_a
        if qid in rows_b and passed(rows_a[qid]) and not passed(rows_b[qid])
    ]
    improvements = [
        qid
        for qid in rows_a
        if qid in rows_b and not passed(rows_a[qid]) and passed(rows_b[qid])
    ]
    lines += ["", f"## 回归题（{a['label']} 通过 → {b['label']} 失败）", ""]
    lines += [f"- {qid}" for qid in sorted(regressions)] or ["（无）"]
    lines += ["", f"## 改善题（{a['label']} 失败 → {b['label']} 通过）", ""]
    lines += [f"- {qid}" for qid in sorted(improvements)] or ["（无）"]
    return "\n".join(lines)


def build_payload(label, config, ingest_info, rows, agg, dataset_version) -> dict:
    """组装 report.json 的完整载荷。"""
    return {
        "label": label,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "config": {**config, "dataset_version": dataset_version},
        "ingest": ingest_info,
        **agg,  # aggregate / by_category / latency
        "per_question": rows,
    }
