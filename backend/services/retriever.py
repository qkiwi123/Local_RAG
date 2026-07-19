"""
统一检索器 — 支持 Dense、BM25 与 RRF Hybrid 三种候选生成模式。

与直接调用 vector_store.search() 相比，本模块增加了：
1. 相似度阈值过滤：过滤掉 distance 过大的结果，避免基于不相关内容"编"答案
2. 同文档去重：同一文档最多保留 N 条，避免一个长文档垄断返回结果
3. 上下文格式化：将检索结果拼接为 LLM 可直接使用的文本

不直接把 Chroma 返回结果丢给 LLM 的原因：
- 需要去重、整理格式、控制 Prompt 长度
- 低质量的相关结果会误导 LLM，必须先过滤
"""

from time import perf_counter
from typing import List

from backend.config import settings
from backend.models.schemas import RetrievalResponse
from backend.services.embedding import embed_query
from backend.services.fusion import reciprocal_rank_fusion
from backend.services.reranker import rerank
from backend.services.retrieval_trace import TraceCollector
from backend.services.sparse_retriever import search as bm25_search
from backend.services.vector_store import (
    search as vector_search,
    search_by_embedding as vector_search_by_embedding,
)


def _candidate_k(top_k: int) -> int:
    """Reranker 开启时取实验候选数，否则保持历史 top_k * 2。"""
    if settings.RERANKER_ENABLED:
        return max(top_k, settings.RERANKER_CANDIDATE_K)
    return top_k * 2


def _dense_candidates(
    question: str,
    candidate_k: int,
    collector: TraceCollector,
) -> List[dict]:
    """生成 Dense 候选；Trace 开启时拆分向量化与 Chroma 耗时。"""
    if collector.enabled:
        started = perf_counter()
        query_embedding = embed_query(question)
        collector.set_timing("query_embedding", perf_counter() - started)
        started = perf_counter()
        raw = vector_search_by_embedding(query_embedding, top_k=candidate_k)
        collector.set_timing("dense_retrieval", perf_counter() - started)
    else:
        raw = vector_search(question, top_k=candidate_k)

    ranked = [
        {**row, "dense_rank": rank}
        for rank, row in enumerate(raw, 1)
    ]
    eligibility = {
        row["chunk_id"]: (
            row["distance"] <= settings.RETRIEVAL_DISTANCE_THRESHOLD,
            (
                None
                if row["distance"] <= settings.RETRIEVAL_DISTANCE_THRESHOLD
                else "distance_above_threshold"
            ),
        )
        for row in ranked
    }
    collector.add_stage("dense", ranked, eligibility=eligibility)
    return [row for row in ranked if eligibility[row["chunk_id"]][0]]


def _bm25_candidates(
    question: str,
    candidate_k: int,
    collector: TraceCollector,
) -> List[dict]:
    """生成 BM25 候选；分支深度可独立配置，融合前截断到候选上限。"""
    started = perf_counter()
    rows = bm25_search(
        question,
        top_k=max(candidate_k, settings.BM25_TOP_K),
    )[:candidate_k]
    collector.set_timing("sparse_retrieval", perf_counter() - started)
    collector.add_stage("bm25", rows)
    return rows


def _generate_candidates(
    question: str,
    candidate_k: int,
    collector: TraceCollector,
) -> List[dict]:
    """按配置生成单路或 RRF 融合候选。"""
    if settings.RETRIEVAL_MODE == "dense":
        return _dense_candidates(question, candidate_k, collector)
    if settings.RETRIEVAL_MODE == "bm25":
        return _bm25_candidates(question, candidate_k, collector)

    dense = _dense_candidates(question, candidate_k, collector)
    sparse = _bm25_candidates(question, candidate_k, collector)
    started = perf_counter()
    fused = reciprocal_rank_fusion(
        dense,
        sparse,
        rrf_k=settings.RRF_K,
        top_k=candidate_k,
    )
    collector.set_timing("rrf", perf_counter() - started)
    collector.add_stage("rrf", fused)
    return fused


def _limit_per_document(rows: List[dict], top_k: int) -> List[dict]:
    """在排序完成后限制单文档占用量并截取 Top K。"""
    max_per_document = 3
    document_counts = {}
    selected = []
    for row in rows:
        metadata = row["metadata"]
        document_key = metadata.get("document_id") or metadata.get(
            "document_name", "unknown"
        )
        if document_counts.get(document_key, 0) < max_per_document:
            selected.append(row)
            document_counts[document_key] = (
                document_counts.get(document_key, 0) + 1
            )
    return selected[:top_k]


def _retrieve_internal(
    question: str,
    top_k: int,
    include_trace: bool,
) -> RetrievalResponse:
    """共享检索流水线；是否捕获 Trace 由调用方显式决定。"""
    collector = TraceCollector(
        enabled=include_trace,
        mode=settings.RETRIEVAL_MODE,
        reranker_enabled=settings.RERANKER_ENABLED,
    )
    candidates = _generate_candidates(
        question,
        _candidate_k(top_k),
        collector,
    )
    if settings.RERANKER_ENABLED:
        started = perf_counter()
        ranked = rerank(question, candidates)
        collector.set_timing("rerank", perf_counter() - started)
        collector.add_stage("reranker", ranked)
    else:
        ranked = candidates

    selected = _limit_per_document(ranked, top_k=top_k)
    collector.add_stage("selected", selected)
    return RetrievalResponse(
        results=selected,
        trace=collector.finish(selected),
    )


def retrieve(question: str, top_k: int = None) -> List[dict]:
    """兼容入口：只返回业务结果，不因服务端开关构造 Trace。"""
    effective_top_k = (
        settings.RETRIEVAL_TOP_K if top_k is None else top_k
    )
    return _retrieve_internal(
        question,
        effective_top_k,
        include_trace=False,
    ).results


def retrieve_with_trace(
    question: str,
    top_k: int = None,
) -> RetrievalResponse:
    """聊天入口：服务端允许时同时返回结构化 Trace。"""
    effective_top_k = (
        settings.RETRIEVAL_TOP_K if top_k is None else top_k
    )
    return _retrieve_internal(
        question,
        effective_top_k,
        include_trace=settings.RETRIEVAL_TRACE_ENABLED,
    )


def format_retrieval_context(results: List[dict]) -> str:
    """
    将多条检索结果拼接成一段结构化的上下文文本，供 LLM 使用。

    格式：
        【来源1】《文档名》第X页
        文本内容...

        【来源2】《文档名》第Y页
        文本内容...

    这种格式便于 LLM 理解和引用的同时，也便于 citation.py 后续解析。

    Args:
        results: retrieve() 返回的结果列表

    Returns:
        格式化后的上下文字符串
    """
    if not results:
        return "（未找到相关文档内容）"

    parts = []
    for i, r in enumerate(results, 1):
        doc_name = r["metadata"].get("document_name", "unknown")
        page = r["metadata"].get("page_number", "?")
        content = r["content"]

        parts.append(f"【来源{i}】《{doc_name}》第{page}页\n{content}")

    return "\n\n".join(parts)
