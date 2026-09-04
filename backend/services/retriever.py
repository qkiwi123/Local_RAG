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

from dataclasses import dataclass, replace
from time import perf_counter
from typing import List

from backend.config import settings
from backend.models.schemas import RetrievalResponse
from backend.models.settings_schemas import RetrievalMode, RetrievalSettings
from backend.services.embedding import embed_query
from backend.services.fusion import reciprocal_rank_fusion
from backend.services.reranker import rerank
from backend.services.retrieval_trace import (
    TraceCollector,
    build_trace_effective_config,
)
from backend.services.sparse_retriever import search as bm25_search
from backend.services.vector_store import (
    search as vector_search,
    search_by_embedding as vector_search_by_embedding,
)


@dataclass(frozen=True)
class RetrievalRuntimeConfig:
    """一次检索请求固定使用的各阶段独立参数。"""

    mode: RetrievalMode
    final_top_k: int
    dense_candidate_k: int
    dense_distance_threshold: float
    bm25_top_k: int
    bm25_k1: float
    bm25_b: float
    rrf_k: int
    reranker_enabled: bool
    reranker_candidate_k: int
    max_chunks_per_document: int
    refusal_gate_enabled: bool = False


def _runtime_config(
    runtime: RetrievalSettings | RetrievalRuntimeConfig | None,
    top_k: int | None,
) -> RetrievalRuntimeConfig:
    """优先使用请求快照；无快照时保持脚本/评测的模块配置入口。"""
    if isinstance(runtime, RetrievalRuntimeConfig):
        config = runtime
    elif runtime is not None:
        config = RetrievalRuntimeConfig(
            mode=runtime.mode,
            final_top_k=runtime.final_top_k,
            dense_candidate_k=runtime.dense_candidate_k,
            dense_distance_threshold=runtime.dense_distance_threshold,
            bm25_top_k=runtime.bm25_top_k,
            bm25_k1=runtime.bm25_k1,
            bm25_b=runtime.bm25_b,
            rrf_k=runtime.rrf_k,
            reranker_enabled=runtime.reranker_enabled,
            reranker_candidate_k=runtime.reranker_candidate_k,
            max_chunks_per_document=runtime.max_chunks_per_document,
            refusal_gate_enabled=runtime.refusal_gate.enabled,
        )
    else:
        final_top_k = (
            settings.RETRIEVAL_TOP_K if top_k is None else top_k
        )
        reranker_enabled = bool(settings.RERANKER_ENABLED)
        reranker_candidate_k = max(
            final_top_k,
            int(settings.RERANKER_CANDIDATE_K),
        )
        dense_candidate_k = getattr(settings, "DENSE_CANDIDATE_K", None)
        if dense_candidate_k is None:
            dense_candidate_k = (
                reranker_candidate_k
                if reranker_enabled
                else final_top_k * 2
            )
        config = RetrievalRuntimeConfig(
            mode=settings.RETRIEVAL_MODE,
            final_top_k=final_top_k,
            dense_candidate_k=max(final_top_k, int(dense_candidate_k)),
            dense_distance_threshold=settings.RETRIEVAL_DISTANCE_THRESHOLD,
            bm25_top_k=max(final_top_k, int(settings.BM25_TOP_K)),
            bm25_k1=float(settings.BM25_K1),
            bm25_b=float(settings.BM25_B),
            rrf_k=int(settings.RRF_K),
            reranker_enabled=reranker_enabled,
            reranker_candidate_k=reranker_candidate_k,
            max_chunks_per_document=min(
                int(getattr(settings, "MAX_CHUNKS_PER_DOCUMENT", 3)),
                final_top_k,
            ),
        )
    if top_k is None or top_k == config.final_top_k:
        return config
    return replace(
        config,
        final_top_k=top_k,
        dense_candidate_k=max(top_k, config.dense_candidate_k),
        bm25_top_k=max(top_k, config.bm25_top_k),
        reranker_candidate_k=max(top_k, config.reranker_candidate_k),
        max_chunks_per_document=min(config.max_chunks_per_document, top_k),
    )


def _dense_candidates(
    question: str,
    candidate_k: int,
    collector: TraceCollector,
    allowed_document_ids: List[str] | None,
    distance_threshold: float,
) -> List[dict]:
    """生成 Dense 候选；Trace 开启时拆分向量化与 Chroma 耗时。"""
    if collector.enabled:
        started = perf_counter()
        query_embedding = embed_query(question)
        collector.set_timing("query_embedding", perf_counter() - started)
        started = perf_counter()
        raw = vector_search_by_embedding(
            query_embedding,
            top_k=candidate_k,
            allowed_document_ids=allowed_document_ids,
        )
        collector.set_timing("dense_retrieval", perf_counter() - started)
    else:
        raw = vector_search(
            question,
            top_k=candidate_k,
            allowed_document_ids=allowed_document_ids,
        )

    ranked = [
        {**row, "dense_rank": rank}
        for rank, row in enumerate(raw, 1)
    ]
    eligibility = {
        row["chunk_id"]: (
            row["distance"] <= distance_threshold,
            (
                None
                if row["distance"] <= distance_threshold
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
    allowed_document_ids: List[str] | None,
    *,
    k1: float,
    b: float,
) -> List[dict]:
    """生成 BM25 候选；分支深度可独立配置，融合前截断到候选上限。"""
    started = perf_counter()
    if collector.enabled:
        diagnostics = {}
        rows = bm25_search(
            question,
            top_k=candidate_k,
            allowed_document_ids=allowed_document_ids,
            k1=k1,
            b=b,
            diagnostics=diagnostics,
        )
        collector.set_sparse_diagnostics(diagnostics)
    else:
        rows = bm25_search(
            question,
            top_k=candidate_k,
            allowed_document_ids=allowed_document_ids,
            k1=k1,
            b=b,
        )
    collector.set_timing("sparse_retrieval", perf_counter() - started)
    collector.add_stage("bm25", rows)
    return rows


def _generate_candidates(
    question: str,
    config: RetrievalRuntimeConfig,
    collector: TraceCollector,
    allowed_document_ids: List[str] | None,
) -> List[dict]:
    """按配置生成单路或 RRF 融合候选。"""
    if config.mode == "dense":
        return _dense_candidates(
            question,
            config.dense_candidate_k,
            collector,
            allowed_document_ids,
            config.dense_distance_threshold,
        )
    if config.mode == "bm25":
        return _bm25_candidates(
            question,
            config.bm25_top_k,
            collector,
            allowed_document_ids,
            k1=config.bm25_k1,
            b=config.bm25_b,
        )

    dense = _dense_candidates(
        question,
        config.dense_candidate_k,
        collector,
        allowed_document_ids,
        config.dense_distance_threshold,
    )
    sparse = _bm25_candidates(
        question,
        config.bm25_top_k,
        collector,
        allowed_document_ids,
        k1=config.bm25_k1,
        b=config.bm25_b,
    )
    started = perf_counter()
    fusion_pool_k = max(
        config.dense_candidate_k,
        config.bm25_top_k,
        (
            config.reranker_candidate_k
            if config.reranker_enabled
            else config.final_top_k
        ),
    )
    fused = reciprocal_rank_fusion(
        dense,
        sparse,
        rrf_k=config.rrf_k,
        top_k=fusion_pool_k,
    )
    collector.set_timing("rrf", perf_counter() - started)
    collector.add_stage("rrf", fused)
    return fused


def _limit_per_document(
    rows: List[dict],
    *,
    top_k: int,
    max_per_document: int,
) -> List[dict]:
    """在排序完成后限制单文档占用量并截取 Top K。"""
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
    config: RetrievalRuntimeConfig,
    include_trace: bool,
    allowed_document_ids: List[str] | None = None,
    tag_filter: List[str] | None = None,
) -> RetrievalResponse:
    """共享检索流水线；是否捕获 Trace 由调用方显式决定。"""
    collector = TraceCollector(
        enabled=include_trace,
        mode=config.mode,
        reranker_enabled=config.reranker_enabled,
        effective_config=(
            build_trace_effective_config(config) if include_trace else None
        ),
        tags=tag_filter,
        allowed_document_count=(
            len(allowed_document_ids)
            if allowed_document_ids is not None
            else None
        ),
    )
    candidates = _generate_candidates(
        question,
        config,
        collector,
        allowed_document_ids,
    )
    if config.reranker_enabled:
        started = perf_counter()
        reranker_input = candidates[:config.reranker_candidate_k]
        ranked = rerank(question, reranker_input)
        collector.set_timing("rerank", perf_counter() - started)
        collector.add_stage("reranker", ranked)
    else:
        ranked = candidates

    selected = _limit_per_document(
        ranked,
        top_k=config.final_top_k,
        max_per_document=config.max_chunks_per_document,
    )
    collector.add_stage("selected", selected)
    return RetrievalResponse(
        results=selected,
        trace=collector.finish(selected),
    )


def retrieve(
    question: str,
    top_k: int = None,
    allowed_document_ids: List[str] | None = None,
    tag_filter: List[str] | None = None,
    runtime_config: RetrievalSettings | RetrievalRuntimeConfig | None = None,
) -> List[dict]:
    """兼容入口：只返回业务结果，不因服务端开关构造 Trace。"""
    config = _runtime_config(runtime_config, top_k)
    return _retrieve_internal(
        question,
        config,
        include_trace=False,
        allowed_document_ids=allowed_document_ids,
        tag_filter=tag_filter,
    ).results


def retrieve_with_trace(
    question: str,
    top_k: int = None,
    allowed_document_ids: List[str] | None = None,
    tag_filter: List[str] | None = None,
    runtime_config: RetrievalSettings | RetrievalRuntimeConfig | None = None,
) -> RetrievalResponse:
    """聊天入口：服务端允许时同时返回结构化 Trace。"""
    config = _runtime_config(runtime_config, top_k)
    return _retrieve_internal(
        question,
        config,
        include_trace=settings.RETRIEVAL_TRACE_ENABLED,
        allowed_document_ids=allowed_document_ids,
        tag_filter=tag_filter,
    )


def format_retrieval_context(results: List[dict]) -> str:
    """
    将多条检索结果拼接成一段结构化的上下文文本，供 LLM 使用。

    格式：
        【来源1】《文档名》来源位置
        文本内容...

        【来源2】《文档名》另一个来源位置
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
        metadata = r["metadata"]
        doc_name = metadata.get("document_name", "unknown")
        location = metadata.get("location_label")
        if not location:
            page = metadata.get("page_number")
            location = f"第{page}页" if page is not None else "位置未知"
        content = r["content"]

        parts.append(f"【来源{i}】《{doc_name}》{location}\n{content}")

    return "\n\n".join(parts)
