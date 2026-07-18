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

from typing import List
from backend.config import settings
from backend.services.fusion import reciprocal_rank_fusion
from backend.services.reranker import rerank
from backend.services.sparse_retriever import search as bm25_search
from backend.services.vector_store import search as vector_search


def _candidate_k(top_k: int) -> int:
    """Reranker 开启时取实验候选数，否则保持历史 top_k * 2。"""
    if settings.RERANKER_ENABLED:
        return max(top_k, settings.RERANKER_CANDIDATE_K)
    return top_k * 2


def _dense_candidates(question: str, candidate_k: int) -> List[dict]:
    """生成通过距离阈值的 Dense 候选，并记录原始 Dense 名次。"""
    return [
        {**row, "dense_rank": dense_rank}
        for dense_rank, row in enumerate(
            vector_search(question, top_k=candidate_k), 1
        )
        if row["distance"] <= settings.RETRIEVAL_DISTANCE_THRESHOLD
    ]


def _bm25_candidates(question: str, candidate_k: int) -> List[dict]:
    """生成 BM25 候选；分支深度可独立配置，融合前截断到候选上限。"""
    return bm25_search(
        question,
        top_k=max(candidate_k, settings.BM25_TOP_K),
    )[:candidate_k]


def _generate_candidates(question: str, candidate_k: int) -> List[dict]:
    """按配置生成单路或 RRF 融合候选。"""
    if settings.RETRIEVAL_MODE == "dense":
        return _dense_candidates(question, candidate_k)
    if settings.RETRIEVAL_MODE == "bm25":
        return _bm25_candidates(question, candidate_k)

    dense = _dense_candidates(question, candidate_k)
    sparse = _bm25_candidates(question, candidate_k)
    return reciprocal_rank_fusion(
        dense,
        sparse,
        rrf_k=settings.RRF_K,
        top_k=candidate_k,
    )


def retrieve(question: str, top_k: int = None) -> List[dict]:
    """生成候选、可选精排，再限制每文档数量并返回 Top K。"""
    if top_k is None:
        top_k = settings.RETRIEVAL_TOP_K

    candidates = _generate_candidates(question, _candidate_k(top_k))
    ranked = (
        rerank(question, candidates)
        if settings.RERANKER_ENABLED
        else candidates
    )

    # 最终结果再限制每文档最多 3 条，避免提前删除 Reranker 可能提升的证据。
    MAX_PER_DOC = 3
    doc_counts = {}
    deduped = []
    for r in ranked:
        metadata = r["metadata"]
        document_key = metadata.get("document_id") or metadata.get(
            "document_name", "unknown"
        )
        if doc_counts.get(document_key, 0) < MAX_PER_DOC:
            deduped.append(r)
            doc_counts[document_key] = doc_counts.get(document_key, 0) + 1

    # 取最终 top_k
    return deduped[:top_k]


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
