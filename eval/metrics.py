"""
指标计算 — 全部为纯函数，不 import backend，可独立单测。

指标口径要点：
1. 检索命中判定 = 文档名相同 AND norm(snippet) ⊆ norm(chunk内容)。
   不用页码判定（虚拟页码会随切分策略变化，snippet 包含判定对切分策略鲁棒）。
2. 引用指标只解析原始回答文本中的 【来源N】 标记，并按 N 映射本次检索结果。
   不直接使用 ChatResponse.citations，确保不存在的来源编号也能被识别为无效引用。
3. norm = NFKC 折叠（全半角统一）+ 去所有空白 + 小写，
   兼容 PDF 解析产生的多余空格和 LLM 回答的全半角混用。
"""

import re
import unicodedata
from typing import List, Optional

# 与 backend/utils/citation.py:_SOURCE_PATTERN 保持一致（该名称为私有，此处复制并注明来源）
SOURCE_PATTERN = re.compile(r"【来源\s*(\d+)】")

# 拒答短语表：回答中出现任一短语即判定为拒答（baseline 跑完后校准一次）
REFUSAL_PHRASES = [
    "未找到与您问题相关的文档内容",  # chat.py 检索为空的固定回复开头
    "没有相关",
    "没有提及",
    "未提及",
    "无法回答",
    "未找到",
    "没有找到",
    "文档中没有",
    "文档中未",
    "不包含",
    "没有包含",
    "没有涉及",
    "未涉及",
    "无相关信息",
]


def norm(s: str) -> str:
    """归一化：NFKC + 去空白 + 小写。所有文本比较前必须先过此函数。"""
    s = unicodedata.normalize("NFKC", s)
    return "".join(s.split()).lower()


def norm_doc_name(name: str) -> str:
    """文档名归一化：去《》书名号、去扩展名（LLM 引用时常省略 .docx/.pdf）。"""
    name = name.strip().strip("《》")
    for ext in (".pdf", ".docx", ".txt", ".doc"):
        if name.lower().endswith(ext):
            name = name[: -len(ext)]
            break
    return norm(name)


def parse_citations(answer: str, retrieval: Optional[List[dict]] = None) -> List[dict]:
    """
    从原始回答解析去重后的 【来源N】。

    提供 retrieval 时，把有效编号映射为真实文档、页码和 chunk_id；不存在的
    编号仍保留为 valid=False，使评估能把伪造来源计入引用假阳性。
    """
    citations = []
    seen = set()
    for match in SOURCE_PATTERN.finditer(answer or ""):
        source_id = int(match.group(1))
        if source_id in seen:
            continue
        seen.add(source_id)

        item = {
            "source_id": source_id,
            "doc": None,
            "page": None,
            "chunk_id": None,
            "valid": False,
        }
        result_index = source_id - 1
        if retrieval is not None and 0 <= result_index < len(retrieval):
            result = retrieval[result_index]
            meta = result.get("metadata") or {}
            doc = meta.get("document_name")
            page = meta.get("page_number")
            if doc and isinstance(page, int):
                item.update({
                    "doc": doc,
                    "page": page,
                    "chunk_id": result.get("chunk_id"),
                    "valid": True,
                })
        citations.append(item)
    return citations


def is_refusal(answer: str) -> bool:
    """判定回答是否为拒答。"""
    return any(p in answer for p in REFUSAL_PHRASES)


def match_evidence(evidence: dict, retrieval: List[dict]) -> Optional[int]:
    """
    在检索结果中查找 evidence 首次命中的位置。

    Returns:
        命中的 0-based 排名；未命中返回 None。
    """
    target_doc = norm_doc_name(evidence["document_name"])
    target_snippet = norm(evidence["snippet"])
    for i, r in enumerate(retrieval):
        doc = norm_doc_name(r["metadata"].get("document_name", ""))
        if doc == target_doc and target_snippet in norm(r["content"]):
            return i
    return None


def score_question(q: dict, answer: str, retrieval: List[dict]) -> dict:
    """
    对单题打分，返回该题的全部原子指标（聚合在 aggregate() 中做）。
    """
    answerable = q["answerable"]
    refused = is_refusal(answer)
    citations = parse_citations(answer, retrieval)
    cited_docs = {
        norm_doc_name(c["doc"])
        for c in citations
        if c["valid"] and c["doc"]
    }

    row = {
        "id": q["id"],
        "category": q["category"],
        "answerable": answerable,
        "refused": refused,
        "citation_count": len(citations),
        "cited_docs": sorted(cited_docs),
    }

    if answerable:
        # ── 检索指标 ──
        ranks = [match_evidence(ev, retrieval) for ev in q["evidence"]]
        matched = [r for r in ranks if r is not None]
        row["evidence_total"] = len(q["evidence"])
        row["evidence_matched"] = len(matched)
        row["hit"] = len(matched) > 0
        row["first_match_rank"] = (min(matched) + 1) if matched else None
        row["recall"] = len(matched) / len(q["evidence"]) if q["evidence"] else 0.0
        row["reciprocal_rank"] = 1.0 / (min(matched) + 1) if matched else 0.0

        # ── 回答关键点 ──
        na = norm(answer)
        groups = q["keypoints"]
        satisfied = sum(1 for g in groups if any(norm(v) in na for v in g))
        row["keypoint_total"] = len(groups)
        row["keypoint_satisfied"] = satisfied
        row["keypoint_coverage"] = satisfied / len(groups) if groups else 0.0
        row["strict_pass"] = bool(groups) and satisfied == len(groups) and not refused

        # ── 引用指标（文档级，只和 gold evidence 文档比） ──
        gold_docs = {norm_doc_name(ev["document_name"]) for ev in q["evidence"]}
        inter = cited_docs & gold_docs
        row["citation_precision"] = (
            len(inter) / len(cited_docs)
            if cited_docs
            else (0.0 if citations else None)
        )
        row["citation_recall"] = len(inter) / len(gold_docs) if gold_docs else None

        # 引用落地性：来源编号是否能映射到本次 retrieval 的真实 metadata。
        # source ID 由本次 retrieval 映射；valid=True 即表示该来源真实落地。
        grounded = sum(1 for c in citations if c["valid"])
        row["citation_grounded"] = grounded
    else:
        # 不可回答题：拒答正确性 + 是否伪造引用
        row["refusal_correct"] = refused
        row["citation_fp"] = len(citations) > 0

    return row


def _percentile(values: List[float], p: float) -> float:
    """最近秩法百分位数（无 numpy 依赖）。"""
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, round(p / 100 * (len(s) - 1))))
    return s[idx]


def _latency_stats(values: List[float]) -> dict:
    if not values:
        return {"mean": 0.0, "p50": 0.0, "p95": 0.0}
    return {
        "mean": round(sum(values) / len(values), 3),
        "p50": round(_percentile(values, 50), 3),
        "p95": round(_percentile(values, 95), 3),
    }


def _aggregate_rows(rows: List[dict]) -> dict:
    """对一组题目行做聚合（供总体与分类别复用）。"""
    ans = [r for r in rows if r["answerable"]]
    una = [r for r in rows if not r["answerable"]]

    def mean(vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    agg = {}
    if ans:
        agg["hit_rate"] = mean([1.0 if r["hit"] else 0.0 for r in ans])
        agg["recall"] = mean([r["recall"] for r in ans])
        agg["mrr"] = mean([r["reciprocal_rank"] for r in ans])
        agg["keypoint_coverage"] = mean([r["keypoint_coverage"] for r in ans])
        agg["strict_accuracy"] = mean([1.0 if r["strict_pass"] else 0.0 for r in ans])
        agg["citation_precision"] = mean([r["citation_precision"] for r in ans])
        agg["citation_recall"] = mean([r["citation_recall"] for r in ans])
        agg["citation_emission_rate"] = mean(
            [1.0 if r["citation_count"] > 0 else 0.0 for r in ans]
        )
        agg["false_refusal_rate"] = mean([1.0 if r["refused"] else 0.0 for r in ans])
    if una:
        agg["refusal_accuracy"] = mean(
            [1.0 if r["refusal_correct"] else 0.0 for r in una]
        )
        agg["unanswerable_citation_fp"] = sum(1 for r in una if r["citation_fp"])
    agg["question_count"] = len(rows)
    return agg


def aggregate(rows: List[dict], timings: List[dict]) -> dict:
    """
    汇总所有题目行 → {"aggregate": ..., "by_category": ..., "latency": ...}。

    Args:
        rows: score_question() 的输出列表
        timings: answer_question() 输出中的耗时字段列表
    """
    by_cat = {}
    for cat in sorted({r["category"] for r in rows}):
        by_cat[cat] = _aggregate_rows([r for r in rows if r["category"] == cat])

    return {
        "aggregate": _aggregate_rows(rows),
        "by_category": by_cat,
        "latency": {
            "retrieval": _latency_stats([t["retrieval_s"] for t in timings]),
            "llm": _latency_stats([t["llm_s"] for t in timings if t["llm_s"] > 0]),
            "total": _latency_stats([t["total_s"] for t in timings]),
        },
    }
