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
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

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
    s = unicodedata.normalize("NFKC", s or "")
    return "".join(s.split()).lower()


def norm_doc_name(name: str) -> str:
    """文档名归一化：去《》书名号、去扩展名（LLM 引用时常省略 .docx/.pdf）。"""
    name = str(name or "").strip().strip("《》")
    for ext in (".pdf", ".docx", ".txt", ".doc", ".md", ".pptx"):
        if name.lower().endswith(ext):
            name = name[: -len(ext)]
            break
    return norm(name)


def _evidence_items(question_or_evidence: Any) -> List[dict]:
    """取得题目的 evidence 列表；也接受直接传入 evidence 列表。"""
    if isinstance(question_or_evidence, dict):
        evidence = question_or_evidence.get("evidence", [])
    else:
        evidence = question_or_evidence
    if not isinstance(evidence, (list, tuple)):
        return []
    return [item for item in evidence if isinstance(item, dict)]


def _retrieval_metadata(result: Any) -> dict:
    if not isinstance(result, dict):
        return {}
    metadata = result.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _retrieval_doc_name(result: Any) -> str:
    metadata = _retrieval_metadata(result)
    # The metadata key is the stable contract.  The direct key fallback keeps
    # old/offline JSONL fixtures usable without changing their meaning.
    return norm_doc_name(metadata.get("document_name", result.get("document_name", "")))


def _retrieval_prefix(retrieval: Sequence[dict], k: Optional[int]) -> Sequence[dict]:
    if k is None:
        return retrieval
    if not isinstance(k, int) or isinstance(k, bool) or k < 0:
        raise ValueError("k 必须是非负整数或 None")
    return retrieval[:k]


def gold_document_names(question_or_evidence: Any) -> Set[str]:
    """返回题目 gold evidence 对应的唯一归一化文档名集合。"""
    return {
        norm_doc_name(evidence.get("document_name", ""))
        for evidence in _evidence_items(question_or_evidence)
        if norm_doc_name(evidence.get("document_name", ""))
    }


def document_metrics(
    question_or_evidence: Any,
    retrieval: Sequence[dict],
    k: Optional[int] = None,
) -> dict:
    """计算文档级 Hit/Recall/MRR 和完整召回。

    文档名先归一化并去重，因此同一 gold 文档包含多条 evidence 时只计一
    个 gold 文档。排名是该 gold 文档首次出现在检索结果中的 1-based 排名。
    ``retrieval`` 默认视为已经按最终排名排序；传 ``k`` 可显式截断到 Top-K。
    没有 gold 文档时返回 0/False，避免把无效的 answerable 题按完美召回计入。
    """
    candidates = _retrieval_prefix(retrieval, k)
    gold_docs = gold_document_names(question_or_evidence)
    retrieved_docs: Set[str] = set()
    first_ranks: Dict[str, int] = {}
    for index, result in enumerate(candidates):
        doc = _retrieval_doc_name(result)
        if not doc:
            continue
        retrieved_docs.add(doc)
        if doc in gold_docs and doc not in first_ranks:
            first_ranks[doc] = index + 1

    matched_docs = gold_docs.intersection(retrieved_docs)
    first_rank = min(first_ranks.values()) if first_ranks else None
    all_recalled = bool(gold_docs) and matched_docs == gold_docs
    multi_document = len(gold_docs) > 1
    return {
        "gold_document_count": len(gold_docs),
        "retrieved_document_count": len(retrieved_docs),
        "matched_document_count": len(matched_docs),
        "matched_document_names": sorted(matched_docs),
        "document_hit_at_k": bool(matched_docs),
        "document_recall_at_k": (
            len(matched_docs) / len(gold_docs) if gold_docs else 0.0
        ),
        "document_first_match_rank": first_rank,
        "document_mrr": 1.0 / first_rank if first_rank else 0.0,
        "all_gold_docs_recalled": all_recalled,
        "multi_document": multi_document,
        "multi_document_complete_recall": all_recalled if multi_document else False,
    }


def document_hit_at_k(
    question_or_evidence: Any, retrieval: Sequence[dict], k: Optional[int] = None
) -> bool:
    return document_metrics(question_or_evidence, retrieval, k)["document_hit_at_k"]


def document_recall_at_k(
    question_or_evidence: Any, retrieval: Sequence[dict], k: Optional[int] = None
) -> float:
    return document_metrics(question_or_evidence, retrieval, k)["document_recall_at_k"]


def document_mrr(
    question_or_evidence: Any, retrieval: Sequence[dict], k: Optional[int] = None
) -> float:
    return document_metrics(question_or_evidence, retrieval, k)["document_mrr"]


def all_gold_docs_recalled(
    question_or_evidence: Any, retrieval: Sequence[dict], k: Optional[int] = None
) -> bool:
    return document_metrics(question_or_evidence, retrieval, k)["all_gold_docs_recalled"]


def multi_document_complete_recall(
    question_or_evidence: Any, retrieval: Sequence[dict], k: Optional[int] = None
) -> bool:
    return document_metrics(question_or_evidence, retrieval, k)[
        "multi_document_complete_recall"
    ]


def _valid_interval(value: Any) -> Optional[Tuple[int, int]]:
    """解析半开区间 [start_char, end_char)，非法/缺失时返回 None。"""
    if not isinstance(value, dict):
        return None
    start = value.get("start_char", value.get("start"))
    end = value.get("end_char", value.get("end"))
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or start < 0
        or end <= start
    ):
        return None
    return start, end


def evidence_locator_interval(evidence: dict) -> Optional[Tuple[int, int]]:
    """读取 evidence 的 start/end offset。

    Dataset v3 当前把 offset 放在 evidence 顶层；为兼容离线候选，也接受
    ``locator``/``location`` 内嵌的 start/end。缺少合法 offset 明确返回 None。
    """
    for candidate in (
        evidence,
        evidence.get("locator") if isinstance(evidence, dict) else None,
        evidence.get("location") if isinstance(evidence, dict) else None,
    ):
        interval = _valid_interval(candidate)
        if interval is not None:
            return interval
    return None


def _location_value(mapping: dict, key: str) -> Any:
    if key in mapping:
        return mapping[key]
    for nested_key in ("locator", "location"):
        nested = mapping.get(nested_key)
        if isinstance(nested, dict) and key in nested:
            return nested[key]
    return None


def _same_source_unit(evidence: dict, metadata: dict) -> bool:
    """若两侧都声明 unit，则避免把不同 unit 的局部 offset 错配。"""
    evidence_unit_index = _location_value(evidence, "unit_index")
    metadata_unit_index = _location_value(metadata, "unit_index")
    if evidence_unit_index is not None and metadata_unit_index is not None:
        # Dataset v3 的 evidence unit_id 是 ``relative/path:uN``，运行时
        # structured parser 则使用基于 document_id 生成的哈希 unit_id；两套
        # 命名空间不可直接比较。双方都有稳定 unit_index 时，以 index 为准。
        return evidence_unit_index == metadata_unit_index

    evidence_unit_id = _location_value(evidence, "unit_id")
    metadata_unit_id = _location_value(metadata, "unit_id")
    if evidence_unit_id and metadata_unit_id:
        return evidence_unit_id == metadata_unit_id
    return True


def evidence_interval_coverage(
    evidence: dict, retrieval: Sequence[dict], k: Optional[int] = None
) -> Optional[float]:
    """计算单条 evidence 被检索 chunk 区间并集覆盖的比例。

    只比较归一化文档名；若两侧都有 unit_id/unit_index，还要求 unit 一致。
    chunk 区间按半开区间处理，并集计算避免重叠 chunk 重复计数。evidence 或
    gold 文档未出现在 retrieval 中，或已出现但合法区间与 gold 无交集时，返回
    真实的 ``0.0``；仅在已检索文档缺少/无法对齐合法 offset 时返回 ``None``。
    后一种情况应查看 exact containment 诊断，而不是把缺失 offset 当成 0% 或
    100%。
    """
    target = evidence_locator_interval(evidence)
    target_doc = norm_doc_name(evidence.get("document_name", ""))
    if target is None or not target_doc:
        return None
    target_start, target_end = target
    intervals: List[Tuple[int, int]] = []
    document_seen = False
    aligned_chunk_seen = False
    valid_offset_seen = False
    for result in _retrieval_prefix(retrieval, k):
        metadata = _retrieval_metadata(result)
        if _retrieval_doc_name(result) != target_doc:
            continue
        document_seen = True
        if not _same_source_unit(evidence, metadata):
            continue
        aligned_chunk_seen = True
        chunk = _valid_interval(metadata)
        if chunk is None:
            continue
        valid_offset_seen = True
        overlap_start = max(target_start, chunk[0])
        overlap_end = min(target_end, chunk[1])
        if overlap_end > overlap_start:
            intervals.append((overlap_start, overlap_end))

    if not intervals:
        # A missing gold document is a real zero, not an unavailable metric.
        # ``None`` is reserved for a retrieved document whose offsets cannot
        # be aligned/calculated (missing or incompatible metadata).
        if not document_seen:
            return 0.0
        if not aligned_chunk_seen or not valid_offset_seen:
            return None
        return 0.0
    intervals.sort()
    covered = 0
    current_start, current_end = intervals[0]
    for start, end in intervals[1:]:
        if start > current_end:
            covered += current_end - current_start
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    covered += current_end - current_start
    return covered / (target_end - target_start)


def evidence_coverage_details(
    question_or_evidence: Any,
    retrieval: Sequence[dict],
    k: Optional[int] = None,
) -> dict:
    """返回题目级 coverage 及 offset 可用性诊断。

    题内对所有可计算 evidence 求均值；真实的 0% 覆盖也计入均值。没有任何
    可计算 evidence 时 ``coverage`` 为 None。``missing_offsets`` 使调用方能
    区分“未覆盖”与“无法按 offset 计算”。
    """
    evidence = _evidence_items(question_or_evidence)
    values = [evidence_interval_coverage(item, retrieval, k) for item in evidence]
    usable = [value for value in values if value is not None]
    return {
        "coverage": sum(usable) / len(usable) if usable else None,
        "values": values,
        "available": len(usable),
        "total": len(values),
        "missing_offsets": len(values) - len(usable),
        "all_available": bool(values) and len(usable) == len(values),
    }


def evidence_coverage(
    question_or_evidence: Any, retrieval: Sequence[dict], k: Optional[int] = None
) -> Optional[float]:
    return evidence_coverage_details(question_or_evidence, retrieval, k)["coverage"]


# More explicit aliases for callers that prefer a verb in the function name.
compute_evidence_coverage = evidence_coverage
coverage_for_evidence = evidence_interval_coverage


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
            structured_location = (
                bool(meta.get("unit_id"))
                and bool(meta.get("location_label"))
                and meta.get("locator_kind")
                in {"page", "virtual_page", "slide", "section", "sheet"}
            )
            if doc and (isinstance(page, int) or structured_location):
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
    target_doc = norm_doc_name(evidence.get("document_name", ""))
    target_snippet = norm(evidence.get("snippet", ""))
    if not target_doc or not target_snippet:
        return None
    for i, r in enumerate(retrieval):
        doc = _retrieval_doc_name(r)
        if doc == target_doc and target_snippet in norm(r.get("content", "")):
            return i
    return None


def exact_evidence_metrics(
    question_or_evidence: Any,
    retrieval: Sequence[dict],
    k: Optional[int] = None,
) -> dict:
    """保留旧的单 chunk 完整 snippet 命中口径，作为诊断指标。"""
    candidates = list(_retrieval_prefix(retrieval, k))
    evidence = _evidence_items(question_or_evidence)
    ranks = [match_evidence(item, candidates) for item in evidence]
    matched = [rank for rank in ranks if rank is not None]
    first_rank = min(matched) + 1 if matched else None
    return {
        "exact_evidence_total": len(evidence),
        "exact_evidence_matched": len(matched),
        "exact_evidence_hit": bool(matched),
        "exact_evidence_recall": (
            len(matched) / len(evidence) if evidence else 0.0
        ),
        "exact_evidence_first_match_rank": first_rank,
        "exact_evidence_reciprocal_rank": 1.0 / first_rank if first_rank else 0.0,
        "exact_evidence_ranks": ranks,
    }


def exact_evidence_containment(
    question_or_evidence: Any, retrieval: Sequence[dict], k: Optional[int] = None
) -> bool:
    """返回是否至少有一条 evidence 被单个 chunk 完整包含。"""
    return exact_evidence_metrics(question_or_evidence, retrieval, k)[
        "exact_evidence_hit"
    ]


def exact_evidence_containment_rate(
    question_or_evidence: Any, retrieval: Sequence[dict], k: Optional[int] = None
) -> float:
    return exact_evidence_metrics(question_or_evidence, retrieval, k)[
        "exact_evidence_recall"
    ]


def score_question(q: dict, answer: str, retrieval: List[dict]) -> dict:
    """
    对单题打分，返回该题的全部原子指标（聚合在 aggregate() 中做）。
    """
    answerable = q["answerable"]
    evidence = _evidence_items(q)
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
        "retrieval_empty": not bool(retrieval),
    }

    if answerable:
        # ── 检索指标 ──
        exact = exact_evidence_metrics(q, retrieval)
        row.update(exact)
        row.update(
            {
                # Legacy fields intentionally retain the exact single-chunk
                # semantics.  New document-level fields below are the robust
                # retrieval quality metrics for Dataset v3.
                "evidence_total": exact["exact_evidence_total"],
                "evidence_matched": exact["exact_evidence_matched"],
                "hit": exact["exact_evidence_hit"],
                "first_match_rank": exact["exact_evidence_first_match_rank"],
                "recall": exact["exact_evidence_recall"],
                "reciprocal_rank": exact["exact_evidence_reciprocal_rank"],
                "exact_evidence_containment": exact["exact_evidence_hit"],
                "exact_evidence_containment_rate": exact["exact_evidence_recall"],
            }
        )

        document = document_metrics(q, retrieval)
        row.update(document)
        row["document_hit"] = document["document_hit_at_k"]
        row["document_recall"] = document["document_recall_at_k"]
        row["document_reciprocal_rank"] = document["document_mrr"]

        coverage = evidence_coverage_details(q, retrieval)
        row.update(
            {
                "evidence_coverage": coverage["coverage"],
                "evidence_coverage_values": coverage["values"],
                "evidence_coverage_available": coverage["available"],
                "evidence_coverage_total": coverage["total"],
                "evidence_coverage_missing_offsets": coverage["missing_offsets"],
                "evidence_coverage_all_available": coverage["all_available"],
                # Keep the fallback explicit instead of silently turning a
                # missing offset into an apparent zero/one score.
                "evidence_coverage_fallback": (
                    "exact_evidence_containment"
                    if coverage["coverage"] is None
                    else None
                ),
            }
        )

        # ── 回答关键点 ──
        na = norm(answer)
        groups = q.get("keypoints", [])
        satisfied = sum(1 for g in groups if any(norm(v) in na for v in g))
        row["keypoint_total"] = len(groups)
        row["keypoint_satisfied"] = satisfied
        row["keypoint_coverage"] = satisfied / len(groups) if groups else 0.0
        row["strict_pass"] = bool(groups) and satisfied == len(groups) and not refused

        # ── 引用指标（文档级，只和 gold evidence 文档比） ──
        gold_docs = gold_document_names(q)
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
        # 不可回答题：拒答正确性 + 是否伪造引用。llm_entry/refusal_gate
        # 仅根据已有 retrieval 判定；这里没有任何 LLM 调用。
        row["llm_entry"] = bool(retrieval)
        row["refusal_gate"] = not bool(retrieval)
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


def aggregate_unanswerable_gates(rows: List[dict]) -> dict:
    """聚合不可回答题的 retrieval-only LLM/refusal gate。

    ``llm_entry`` 表示已有检索结果会让链路进入生成阶段；``refusal_gate``
    表示检索层是否可以直接拒答。两者都可由离线 retrieval 结果计算，故该
    接口不接收也不调用 LLM。若调用方没有提供对应字段，返回 None 而不是
    把缺失数据当成通过。
    """
    unanswerable = [row for row in rows if not row.get("answerable", True)]
    result = {"unanswerable_count": len(unanswerable)}
    llm_rows = [row for row in unanswerable if "llm_entry" in row]
    if llm_rows:
        entry_count = sum(1 for row in llm_rows if bool(row.get("llm_entry")))
        result.update(
            {
                "unanswerable_llm_entry_count": entry_count,
                "unanswerable_llm_entry_rate": round(
                    entry_count / len(llm_rows), 4
                ),
            }
        )
    else:
        result.update(
            {
                "unanswerable_llm_entry_count": None,
                "unanswerable_llm_entry_rate": None,
            }
        )

    gate_values = []
    for row in unanswerable:
        if "refusal_gate" in row:
            gate_values.append(1.0 if row["refusal_gate"] else 0.0)
        elif "llm_entry" in row:
            gate_values.append(0.0 if row["llm_entry"] else 1.0)
    gate_rate = (
        round(sum(gate_values) / len(gate_values), 4) if gate_values else None
    )
    result.update(
        {
            "unanswerable_refusal_gate_count": (
                sum(gate_values) if gate_values else None
            ),
            "unanswerable_refusal_gate_rate": gate_rate,
            "unanswerable_refusal_gate_accuracy": gate_rate,
            "refusal_gate_accuracy": gate_rate,
        }
    )

    refusal_values = [
        1.0 if row.get("refusal_correct") else 0.0
        for row in unanswerable
        if "refusal_correct" in row
    ]
    result["unanswerable_refusal_accuracy"] = (
        round(sum(refusal_values) / len(refusal_values), 4)
        if refusal_values
        else None
    )
    return result


def _aggregate_rows(rows: List[dict]) -> dict:
    """对一组题目行做聚合（供总体与分类别复用）。"""
    ans = [r for r in rows if r.get("answerable")]
    una = [r for r in rows if not r.get("answerable")]

    def mean(vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    agg = {}
    if ans:
        agg["hit_rate"] = mean([1.0 if r.get("hit") else 0.0 for r in ans])
        agg["recall"] = mean([r.get("recall") for r in ans])
        agg["mrr"] = mean([r.get("reciprocal_rank") for r in ans])

        # Corrected Dataset v3 retrieval metrics.  The legacy keys above stay
        # intact and continue to describe exact evidence containment.
        agg["document_hit_rate"] = mean(
            [
                1.0
                if r.get("document_hit_at_k", r.get("document_hit", r.get("hit")))
                else 0.0
                for r in ans
            ]
        )
        agg["document_recall"] = mean(
            [
                r.get("document_recall_at_k", r.get("document_recall", r.get("recall")))
                for r in ans
            ]
        )
        agg["document_recall_rate"] = agg["document_recall"]
        agg["document_mrr"] = mean(
            [
                r.get(
                    "document_mrr",
                    r.get("document_reciprocal_rank", r.get("reciprocal_rank")),
                )
                for r in ans
            ]
        )
        agg["all_gold_docs_recalled_rate"] = mean(
            [1.0 if r.get("all_gold_docs_recalled") else 0.0 for r in ans]
        )
        multi = [
            r
            for r in ans
            if r.get("multi_document")
            or (r.get("gold_document_count") or 0) > 1
        ]
        agg["multi_document_count"] = len(multi)
        agg["multi_document_complete_recall_rate"] = mean(
            [
                1.0
                if r.get(
                    "multi_document_complete_recall",
                    r.get("all_gold_docs_recalled"),
                )
                else 0.0
                for r in multi
            ]
        )

        agg["exact_evidence_containment_rate"] = mean(
            [
                1.0
                if r.get("exact_evidence_containment", r.get("hit"))
                else 0.0
                for r in ans
            ]
        )
        agg["exact_evidence_recall"] = mean(
            [r.get("exact_evidence_recall", r.get("recall")) for r in ans]
        )
        agg["evidence_coverage"] = mean(
            [r.get("evidence_coverage") for r in ans]
        )
        coverage_total = sum(r.get("evidence_coverage_total", 0) or 0 for r in ans)
        coverage_available = sum(
            r.get("evidence_coverage_available", 0) or 0 for r in ans
        )
        agg["evidence_coverage_available_rate"] = (
            round(coverage_available / coverage_total, 4)
            if coverage_total
            else None
        )

        agg["keypoint_coverage"] = mean(
            [r.get("keypoint_coverage") for r in ans]
        )
        agg["strict_accuracy"] = mean(
            [1.0 if r.get("strict_pass") else 0.0 for r in ans]
        )
        agg["citation_precision"] = mean(
            [r.get("citation_precision") for r in ans]
        )
        agg["citation_recall"] = mean([r.get("citation_recall") for r in ans])
        agg["citation_emission_rate"] = mean(
            [1.0 if r.get("citation_count", 0) > 0 else 0.0 for r in ans]
        )
        agg["false_refusal_rate"] = mean(
            [1.0 if r.get("refused") else 0.0 for r in ans]
        )
    if una:
        agg["refusal_accuracy"] = mean(
            [
                1.0 if r.get("refusal_correct") else 0.0
                for r in una
                if "refusal_correct" in r
            ]
        )
        agg["unanswerable_citation_fp"] = sum(
            1 for r in una if r.get("citation_fp")
        )
        agg.update(aggregate_unanswerable_gates(una))
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
