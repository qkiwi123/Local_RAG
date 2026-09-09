"""检索 Trace 构造：快照候选而不修改业务检索结果。"""

from typing import Any, Literal

from backend.models.schemas import (
    RetrievalContextStats,
    RetrievalTrace,
    RetrievalTraceCandidate,
    RetrievalTraceChunk,
    RetrievalTraceEffectiveConfig,
    RetrievalTraceLLMSummary,
    RetrievalTraceStage,
    RetrievalTraceTimings,
)
from backend.models.settings_schemas import EffectiveSettingsSnapshot
from backend.services.embedding import count_tokens


TIMING_FIELDS = (
    "query_embedding",
    "dense_retrieval",
    "sparse_retrieval",
    "rrf",
    "rerank",
    "context_formatting",
    "llm_generation",
    "total",
)


def build_trace_effective_config(config: Any) -> RetrievalTraceEffectiveConfig:
    """从已解析的请求级检索配置生成无密钥 Trace 快照。"""
    refusal_gate = getattr(config, "refusal_gate", None)
    return RetrievalTraceEffectiveConfig(
        retrieval_mode=config.mode,
        final_top_k=config.final_top_k,
        dense_candidate_k=config.dense_candidate_k,
        dense_distance_threshold=config.dense_distance_threshold,
        bm25_top_k=config.bm25_top_k,
        bm25_k1=config.bm25_k1,
        bm25_b=config.bm25_b,
        rrf_k=config.rrf_k,
        reranker_enabled=config.reranker_enabled,
        reranker_candidate_k=config.reranker_candidate_k,
        max_chunks_per_document=config.max_chunks_per_document,
        refusal_gate_enabled=(
            refusal_gate.enabled
            if refusal_gate is not None
            else bool(getattr(config, "refusal_gate_enabled", False))
        ),
    )


class TraceCollector:
    """按执行顺序收集阶段快照，并按 chunk_id 去重保存完整原文。"""

    def __init__(
        self,
        *,
        enabled: bool,
        mode: str,
        reranker_enabled: bool,
        effective_config: RetrievalTraceEffectiveConfig | None = None,
        tags: list[str] | None = None,
        allowed_document_count: int | None = None,
    ):
        self.enabled = enabled
        self.mode = mode
        self.reranker_enabled = reranker_enabled
        self.effective_config = effective_config
        self.tags = list(tags or [])
        self.allowed_document_count = allowed_document_count
        self.chunks: dict[str, RetrievalTraceChunk] = {}
        self.stages: list[RetrievalTraceStage] = []
        self.timings = {field: 0.0 for field in TIMING_FIELDS}
        self.sparse_diagnostics: dict[str, Any] = {}

    def set_timing(self, name: str, elapsed_seconds: float) -> None:
        """记录秒级测量值，并以毫秒写入 Trace。"""
        if self.enabled:
            self.timings[name] = round(elapsed_seconds * 1000, 3)

    def add_stage(
        self,
        name: str,
        rows: list[dict],
        eligibility: dict[str, tuple[bool, str | None]] | None = None,
    ) -> None:
        """复制一个候选阶段；关闭 Trace 时不保留任何行或原文。"""
        if not self.enabled:
            return

        eligibility = eligibility or {}
        candidates = []
        for rank, row in enumerate(rows, 1):
            chunk_id = row["chunk_id"]
            metadata = row.get("metadata") or {}
            self.chunks.setdefault(
                chunk_id,
                RetrievalTraceChunk(
                    chunk_id=chunk_id,
                    document_name=metadata.get("document_name", "unknown"),
                    location_label=metadata.get("location_label", ""),
                    page_number=metadata.get("page_number"),
                    slide_number=metadata.get("slide_number"),
                    content=row.get("content", ""),
                ),
            )
            eligible, reason = eligibility.get(chunk_id, (True, None))
            candidates.append(
                RetrievalTraceCandidate(
                    chunk_id=chunk_id,
                    rank=rank,
                    distance=row.get("distance"),
                    dense_rank=row.get("dense_rank"),
                    bm25_rank=row.get("bm25_rank"),
                    bm25_score=row.get("bm25_score"),
                    rrf_score=row.get("rrf_score"),
                    reranker_score=row.get("reranker_score"),
                    eligible=eligible,
                    filtered_reason=reason,
                )
            )
        self.stages.append(
            RetrievalTraceStage(name=name, candidates=candidates)
        )

    def set_sparse_diagnostics(self, diagnostics: dict[str, Any]) -> None:
        if self.enabled:
            self.sparse_diagnostics = dict(diagnostics)

    def finish(self, selected_rows: list[dict]) -> RetrievalTrace | None:
        """标记最终入选 chunk 并生成不可影响业务结果的 Trace 模型。"""
        if not self.enabled:
            return None

        selected_ids = [row["chunk_id"] for row in selected_rows]
        selected = set(selected_ids)
        stages = [
            stage.model_copy(
                update={
                    "candidates": [
                        candidate.model_copy(
                            update={
                                "selected": candidate.chunk_id in selected
                            }
                        )
                        for candidate in stage.candidates
                    ]
                }
            )
            for stage in self.stages
        ]
        return RetrievalTrace(
            retrieval_mode=self.mode,
            reranker_enabled=self.reranker_enabled,
            effective_config=self.effective_config,
            chunks=self.chunks,
            stages=stages,
            selected_chunk_ids=selected_ids,
            context=RetrievalContextStats(chunk_count=len(selected_ids)),
            timings_ms=RetrievalTraceTimings(**self.timings),
            tag_filter=self.tags,
            filtered_document_count=self.allowed_document_count,
            **self.sparse_diagnostics,
        )


def finalize_chat_trace(
    trace: RetrievalTrace | None,
    *,
    context: str,
    context_formatting_ms: float,
    llm_generation_ms: float,
    followup_rewrite_ms: float | None = None,
    followup_rewrite_status: Literal[
        "not_applicable", "succeeded", "failed"
    ] = "not_applicable",
    citation_extraction_ms: float | None = None,
    time_to_first_delta_ms: float | None = None,
    delta_window_ms: float | None = None,
    total_ms: float,
    runtime: EffectiveSettingsSnapshot | None = None,
    llm_called: bool | None = None,
) -> RetrievalTrace | None:
    """聊天完成后补齐配置版本、模型摘要、上下文规模和路由层耗时。"""
    if trace is None:
        return None

    trace.context = RetrievalContextStats(
        chunk_count=len(trace.selected_chunk_ids),
        char_count=len(context),
        token_count=count_tokens(context) if context else 0,
    )
    trace.timings_ms.context_formatting = round(context_formatting_ms, 3)
    trace.timings_ms.llm_generation = round(llm_generation_ms, 3)
    trace.timings_ms.followup_rewrite = _optional_milliseconds(
        followup_rewrite_ms
    )
    trace.timings_ms.citation_extraction = _optional_milliseconds(
        citation_extraction_ms
    )
    trace.timings_ms.time_to_first_delta = _optional_milliseconds(
        time_to_first_delta_ms
    )
    trace.timings_ms.delta_window = _optional_milliseconds(delta_window_ms)
    trace.timings_ms.total = round(total_ms, 3)
    trace.timing_contract_version = "chat-v2"
    trace.followup_rewrite_status = followup_rewrite_status
    if runtime is not None:
        trace.settings_revision = runtime.revision
        if trace.effective_config is None:
            trace.effective_config = build_trace_effective_config(
                runtime.retrieval
            )
        trace.llm = RetrievalTraceLLMSummary(
            called=bool(llm_called),
            provider=runtime.llm.provider,
            model=runtime.llm.model,
        )
    return trace


def _optional_milliseconds(value: float | None) -> float | None:
    """保留未执行的 None；执行过的0毫秒仍是有效测量。"""
    return None if value is None else round(max(0.0, value), 3)
