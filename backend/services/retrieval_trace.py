"""检索 Trace 构造：快照候选而不修改业务检索结果。"""

from backend.models.schemas import (
    RetrievalContextStats,
    RetrievalTrace,
    RetrievalTraceCandidate,
    RetrievalTraceChunk,
    RetrievalTraceStage,
    RetrievalTraceTimings,
)
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


class TraceCollector:
    """按执行顺序收集阶段快照，并按 chunk_id 去重保存完整原文。"""

    def __init__(
        self,
        *,
        enabled: bool,
        mode: str,
        reranker_enabled: bool,
        tags: list[str] | None = None,
        allowed_document_count: int | None = None,
    ):
        self.enabled = enabled
        self.mode = mode
        self.reranker_enabled = reranker_enabled
        self.tags = list(tags or [])
        self.allowed_document_count = allowed_document_count
        self.chunks: dict[str, RetrievalTraceChunk] = {}
        self.stages: list[RetrievalTraceStage] = []
        self.timings = {field: 0.0 for field in TIMING_FIELDS}

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
            chunks=self.chunks,
            stages=stages,
            selected_chunk_ids=selected_ids,
            context=RetrievalContextStats(chunk_count=len(selected_ids)),
            timings_ms=RetrievalTraceTimings(**self.timings),
            tag_filter=self.tags,
            filtered_document_count=self.allowed_document_count,
        )


def finalize_chat_trace(
    trace: RetrievalTrace | None,
    *,
    context: str,
    context_formatting_ms: float,
    llm_generation_ms: float,
    total_ms: float,
) -> RetrievalTrace | None:
    """聊天完成后补齐上下文规模和路由层耗时。"""
    if trace is None:
        return None

    trace.context = RetrievalContextStats(
        chunk_count=len(trace.selected_chunk_ids),
        char_count=len(context),
        token_count=count_tokens(context) if context else 0,
    )
    trace.timings_ms.context_formatting = round(context_formatting_ms, 3)
    trace.timings_ms.llm_generation = round(llm_generation_ms, 3)
    trace.timings_ms.total = round(total_ms, 3)
    return trace
