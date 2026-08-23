"""P2-3 检索链路 Trace 配置、契约与行为测试。"""

import pytest

from backend.config import _env_bool, settings
from backend.models.schemas import (
    RetrievalContextStats,
    RetrievalResponse,
    RetrievalTrace,
    RetrievalTraceCandidate,
    RetrievalTraceChunk,
    RetrievalTraceStage,
    RetrievalTraceTimings,
)
from backend.services.retrieval_trace import TraceCollector, finalize_chat_trace


def test_retrieval_trace_is_disabled_in_tests():
    assert settings.RETRIEVAL_TRACE_ENABLED is False


def test_env_bool_rejects_invalid_trace_value(monkeypatch):
    monkeypatch.setenv("RETRIEVAL_TRACE_ENABLED", "sometimes")

    with pytest.raises(ValueError, match="RETRIEVAL_TRACE_ENABLED"):
        _env_bool("RETRIEVAL_TRACE_ENABLED")


def test_retrieval_trace_contract_keeps_full_chunk_once():
    trace = RetrievalTrace(
        retrieval_mode="dense",
        reranker_enabled=True,
        chunks={
            "c1": RetrievalTraceChunk(
                chunk_id="c1",
                document_name="doc.txt",
                page_number=1,
                content="完整 chunk 原文",
            )
        },
        stages=[
            RetrievalTraceStage(
                name="dense",
                candidates=[
                    RetrievalTraceCandidate(
                        chunk_id="c1",
                        rank=1,
                        distance=0.2,
                        eligible=True,
                        selected=True,
                    )
                ],
            )
        ],
        selected_chunk_ids=["c1"],
        context=RetrievalContextStats(),
        timings_ms=RetrievalTraceTimings(),
    )
    response = RetrievalResponse(results=[], trace=trace)

    assert response.trace.chunks["c1"].content == "完整 chunk 原文"
    assert response.trace.stages[0].candidates[0].distance == 0.2


def _row(chunk_id, content="完整正文", distance=0.2):
    return {
        "chunk_id": chunk_id,
        "content": content,
        "metadata": {"document_name": "doc.txt", "page_number": 1},
        "distance": distance,
        "dense_rank": 1,
    }


def test_collector_deduplicates_full_chunk_across_stages():
    collector = TraceCollector(enabled=True, mode="dense", reranker_enabled=True)
    collector.add_stage("dense", [_row("c1")])
    collector.add_stage(
        "reranker", [{**_row("c1"), "reranker_score": 2.5}]
    )
    collector.add_stage("selected", [_row("c1")])

    trace = collector.finish([_row("c1")])

    assert list(trace.chunks) == ["c1"]
    assert [stage.name for stage in trace.stages] == [
        "dense",
        "reranker",
        "selected",
    ]
    assert all(stage.candidates[0].selected for stage in trace.stages)


def test_collector_records_dense_threshold_rejection_without_mutating_row():
    row = _row("filtered", distance=0.8)
    collector = TraceCollector(
        enabled=True, mode="dense", reranker_enabled=False
    )
    collector.add_stage(
        "dense",
        [row],
        eligibility={"filtered": (False, "distance_above_threshold")},
    )

    trace = collector.finish([])

    candidate = trace.stages[0].candidates[0]
    assert candidate.eligible is False
    assert candidate.filtered_reason == "distance_above_threshold"
    assert "eligible" not in row


def test_disabled_collector_never_retains_chunk_content():
    collector = TraceCollector(
        enabled=False, mode="dense", reranker_enabled=False
    )
    collector.add_stage("dense", [_row("c1")])

    assert collector.finish([_row("c1")]) is None
    assert collector.chunks == {}


def test_finalize_chat_trace_records_context_and_llm_timing(monkeypatch):
    collector = TraceCollector(
        enabled=True, mode="dense", reranker_enabled=False
    )
    trace = collector.finish([])
    monkeypatch.setattr(
        "backend.services.retrieval_trace.count_tokens", lambda text: 9
    )

    completed = finalize_chat_trace(
        trace,
        context="上下文",
        context_formatting_ms=1.2,
        llm_generation_ms=3.4,
        total_ms=5.6,
    )

    assert completed.context.char_count == 3
    assert completed.context.token_count == 9
    assert completed.timings_ms.llm_generation == 3.4
    assert completed.timings_ms.total == 5.6


def test_hybrid_trace_has_only_executed_stages_and_full_chunks(monkeypatch):
    from backend.services import retriever

    monkeypatch.setattr(settings, "RETRIEVAL_TRACE_ENABLED", True)
    monkeypatch.setattr(settings, "RETRIEVAL_MODE", "hybrid")
    monkeypatch.setattr(settings, "RERANKER_ENABLED", True)
    monkeypatch.setattr(settings, "RERANKER_CANDIDATE_K", 2)
    monkeypatch.setattr(settings, "BM25_TOP_K", 2)
    monkeypatch.setattr(settings, "RETRIEVAL_TOP_K", 1)
    monkeypatch.setattr(retriever, "embed_query", lambda question: [0.1])
    monkeypatch.setattr(
        retriever,
        "vector_search_by_embedding",
        lambda vector, top_k, allowed_document_ids=None: [
            _row("shared"),
            _row("filtered", distance=0.8),
        ],
    )
    monkeypatch.setattr(
        retriever,
        "bm25_search",
        lambda question, top_k, allowed_document_ids=None, **kwargs: [
            {**_row("shared"), "bm25_rank": 1, "bm25_score": 5.0}
        ],
    )
    monkeypatch.setattr(
        retriever,
        "reciprocal_rank_fusion",
        lambda dense, sparse, rrf_k, top_k: [
            {
                **dense[0],
                "bm25_rank": 1,
                "bm25_score": 5.0,
                "rrf_score": 0.03,
            }
        ],
    )
    monkeypatch.setattr(
        retriever,
        "rerank",
        lambda question, rows: [
            {**rows[0], "reranker_score": 2.0}
        ],
    )

    response = retriever.retrieve_with_trace("问题", top_k=1)

    assert [stage.name for stage in response.trace.stages] == [
        "dense",
        "bm25",
        "rrf",
        "reranker",
        "selected",
    ]
    assert set(response.trace.chunks) == {"shared", "filtered"}
    dense = response.trace.stages[0].candidates
    assert dense[1].eligible is False
    assert response.trace.selected_chunk_ids == ["shared"]


def test_plain_retrieve_never_builds_trace_even_when_server_flag_is_on(
    monkeypatch,
):
    from backend.services import retriever

    monkeypatch.setattr(settings, "RETRIEVAL_TRACE_ENABLED", True)
    monkeypatch.setattr(settings, "RETRIEVAL_MODE", "dense")
    monkeypatch.setattr(settings, "RERANKER_ENABLED", False)
    monkeypatch.setattr(
        retriever,
        "vector_search",
        lambda question, top_k, allowed_document_ids=None: [_row("c1")],
    )

    assert [
        row["chunk_id"] for row in retriever.retrieve("问题", top_k=1)
    ] == ["c1"]


def test_bm25_trace_omits_dense_and_rrf_stages(monkeypatch):
    from backend.services import retriever

    monkeypatch.setattr(settings, "RETRIEVAL_TRACE_ENABLED", True)
    monkeypatch.setattr(settings, "RETRIEVAL_MODE", "bm25")
    monkeypatch.setattr(settings, "RERANKER_ENABLED", False)
    monkeypatch.setattr(
        retriever,
        "bm25_search",
        lambda question, top_k, allowed_document_ids=None, **kwargs: [
            {**_row("c1"), "bm25_rank": 1, "bm25_score": 2.0}
        ],
    )

    response = retriever.retrieve_with_trace("问题", top_k=1)

    assert [stage.name for stage in response.trace.stages] == [
        "bm25",
        "selected",
    ]
    assert response.trace.timings_ms.query_embedding == 0.0
