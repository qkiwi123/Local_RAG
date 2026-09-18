"""P2-3 检索链路 Trace 配置、契约与行为测试。"""

import pytest
from pydantic import SecretStr, ValidationError

from backend.config import _env_bool, settings
from backend.models.schemas import (
    RetrievalContextStats,
    RetrievalResponse,
    RetrievalTrace,
    RetrievalTraceCandidate,
    RetrievalTraceChunk,
    RetrievalTraceLLMSummary,
    RetrievalTraceStage,
    RetrievalTraceTimings,
)
from backend.models.settings_schemas import (
    EffectiveSettingsSnapshot,
    LLMSettings,
    RetrievalSettings,
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
        followup_rewrite_ms=0.8,
        followup_rewrite_status="succeeded",
        citation_extraction_ms=0.6,
        time_to_first_delta_ms=4.2,
        delta_window_ms=1.1,
        total_ms=5.6,
    )

    assert completed.context.char_count == 3
    assert completed.context.token_count == 9
    assert completed.timings_ms.llm_generation == 3.4
    assert completed.timings_ms.followup_rewrite == 0.8
    assert completed.timings_ms.citation_extraction == 0.6
    assert completed.timings_ms.persistence is None
    assert completed.timings_ms.time_to_first_delta == 4.2
    assert completed.timings_ms.delta_window == 1.1
    assert completed.timings_ms.total == 5.6
    assert completed.timing_contract_version == "chat-v2"
    assert completed.followup_rewrite_status == "succeeded"


def test_legacy_trace_without_v2_timings_remains_readable():
    legacy = RetrievalTrace.model_validate(
        {
            "retrieval_mode": "dense",
            "reranker_enabled": False,
            "timings_ms": {
                "query_embedding": 1.0,
                "dense_retrieval": 2.0,
                "total": 5.0,
            },
        }
    )

    assert legacy.timing_contract_version is None
    assert legacy.followup_rewrite_status is None
    assert legacy.timings_ms.followup_rewrite is None
    assert legacy.timings_ms.citation_extraction is None
    assert legacy.timings_ms.persistence is None
    assert legacy.timings_ms.time_to_first_delta is None
    assert legacy.timings_ms.delta_window is None


def test_finalize_chat_trace_records_runtime_without_secret(monkeypatch):
    secret = "trace-must-not-contain-this-secret"
    runtime = EffectiveSettingsSnapshot(
        revision=7,
        llm=LLMSettings(
            enabled=False,
            provider="deepseek",
            base_url="https://api.example.com",
            model="example-model",
            temperature=0.2,
            max_tokens=100,
            timeout_seconds=30,
        ),
        retrieval=RetrievalSettings(
            mode="hybrid",
            final_top_k=4,
            dense_candidate_k=12,
            dense_distance_threshold=0.31,
            bm25_top_k=9,
            bm25_k1=1.2,
            bm25_b=0.6,
            rrf_k=50,
            reranker_enabled=True,
            reranker_candidate_k=8,
            max_chunks_per_document=2,
        ),
        api_key=SecretStr(secret),
        api_key_source="runtime_secret",
        field_sources={},
    )
    trace = TraceCollector(
        enabled=True,
        mode="hybrid",
        reranker_enabled=True,
    ).finish([])
    monkeypatch.setattr(
        "backend.services.retrieval_trace.count_tokens", lambda text: 0
    )

    completed = finalize_chat_trace(
        trace,
        context="",
        context_formatting_ms=0,
        llm_generation_ms=0,
        total_ms=1,
        runtime=runtime,
        llm_called=False,
    )
    payload = completed.model_dump(mode="json")

    assert payload["settings_revision"] == 7
    assert payload["effective_config"] == {
        "retrieval_mode": "hybrid",
        "final_top_k": 4,
        "dense_candidate_k": 12,
        "dense_distance_threshold": 0.31,
        "bm25_top_k": 9,
        "bm25_k1": 1.2,
        "bm25_b": 0.6,
        "rrf_k": 50,
        "reranker_enabled": True,
        "reranker_candidate_k": 8,
        "max_chunks_per_document": 2,
        "refusal_gate_enabled": False,
    }
    assert payload["llm"] == {
        "called": False,
        "provider": "deepseek",
        "model": "example-model",
        "answer": {
            "called": False,
            "thinking_mode": "disabled",
            "reasoning_effort": "provider_default",
            "temperature": 0.2,
            "max_tokens": 100,
            "timeout_seconds": 30.0,
        },
        "rewrite": {
            "called": False,
            "thinking_mode": "disabled",
            "reasoning_effort": "provider_default",
            "temperature": 0.0,
            "max_tokens": 200,
            "timeout_seconds": 30.0,
        },
    }
    serialized = completed.model_dump_json()
    assert secret not in serialized
    assert "api_key" not in serialized.lower()


def test_trace_llm_summary_rejects_secret_fields():
    with pytest.raises(ValidationError):
        RetrievalTraceLLMSummary(
            called=True,
            provider="deepseek",
            model="example-model",
            api_key="forbidden",
        )


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


def test_bm25_trace_records_sparse_backend_without_changing_results(monkeypatch):
    from backend.services import retriever

    monkeypatch.setattr(settings, "RETRIEVAL_TRACE_ENABLED", True)
    monkeypatch.setattr(settings, "RETRIEVAL_MODE", "bm25")
    monkeypatch.setattr(settings, "RERANKER_ENABLED", False)

    def fake_bm25(question, top_k, allowed_document_ids=None, **kwargs):
        diagnostics = kwargs.get("diagnostics")
        if diagnostics is not None:
            diagnostics.update(
                {
                    "sparse_backend": "bm25s_mmap",
                    "sparse_index_generation": "generation-1",
                    "sparse_manifest_hash": "abc123",
                    "sparse_index_status": "ready",
                    "sparse_fallback_reason": None,
                    "sparse_index_load_ms": 4.5,
                    "sparse_contract_version": "contract-v1",
                }
            )
        return [{**_row("c1"), "bm25_rank": 1, "bm25_score": 2.0}]

    monkeypatch.setattr(retriever, "bm25_search", fake_bm25)

    plain = retriever.retrieve("问题", top_k=1)
    traced = retriever.retrieve_with_trace("问题", top_k=1)

    assert [row["chunk_id"] for row in plain] == ["c1"]
    assert [row["chunk_id"] for row in traced.results] == ["c1"]
    assert traced.trace.sparse_backend == "bm25s_mmap"
    assert traced.trace.sparse_index_generation == "generation-1"
    assert traced.trace.sparse_manifest_hash == "abc123"
    assert traced.trace.sparse_index_status == "ready"
    assert traced.trace.sparse_fallback_reason is None
    assert traced.trace.sparse_index_load_ms == 4.5
    assert traced.trace.sparse_contract_version == "contract-v1"
