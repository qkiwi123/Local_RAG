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
