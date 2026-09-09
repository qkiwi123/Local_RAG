"""无 LLM 评测接入 RetrievalTrace 的接缝测试。"""

import json
from types import SimpleNamespace

from backend.config import settings
from backend.models.schemas import (
    RetrievalResponse,
    RetrievalTrace,
    RetrievalTraceCandidate,
    RetrievalTraceChunk,
    RetrievalTraceStage,
    RetrievalTraceTimings,
)
from backend.services import embedding, reranker, retriever
from eval import adapters
from eval import eval as eval_cli


def _row(chunk_id: str = "c1", content: str = "证据一") -> dict:
    return {
        "chunk_id": chunk_id,
        "content": content,
        "metadata": {
            "document_id": "doc-1",
            "document_name": "doc.txt",
            "page_number": 1,
            "start_char": 0,
            "end_char": 3,
        },
        "distance": 0.2,
    }


def _trace() -> RetrievalTrace:
    return RetrievalTrace(
        retrieval_mode="hybrid",
        reranker_enabled=True,
        chunks={
            "c1": RetrievalTraceChunk(
                chunk_id="c1",
                document_name="doc.txt",
                page_number=1,
                content="证据一",
            ),
            "filtered": RetrievalTraceChunk(
                chunk_id="filtered",
                document_name="noise.txt",
                page_number=1,
                content="噪声",
            ),
        },
        stages=[
            RetrievalTraceStage(
                name="dense",
                candidates=[
                    RetrievalTraceCandidate(
                        chunk_id="c1",
                        rank=1,
                        distance=0.2,
                        dense_rank=1,
                        selected=True,
                    ),
                    RetrievalTraceCandidate(
                        chunk_id="filtered",
                        rank=2,
                        distance=0.8,
                        dense_rank=2,
                        eligible=False,
                        filtered_reason="distance_above_threshold",
                    ),
                ],
            ),
            RetrievalTraceStage(
                name="bm25",
                candidates=[
                    RetrievalTraceCandidate(
                        chunk_id="c1",
                        rank=1,
                        bm25_rank=1,
                        bm25_score=4.5,
                        selected=True,
                    )
                ],
            ),
            RetrievalTraceStage(
                name="rrf",
                candidates=[
                    RetrievalTraceCandidate(
                        chunk_id="c1",
                        rank=1,
                        rrf_score=0.031,
                        selected=True,
                    )
                ],
            ),
            RetrievalTraceStage(
                name="reranker",
                candidates=[
                    RetrievalTraceCandidate(
                        chunk_id="c1",
                        rank=1,
                        reranker_score=2.4,
                        selected=True,
                    )
                ],
            ),
            RetrievalTraceStage(
                name="selected",
                candidates=[
                    RetrievalTraceCandidate(
                        chunk_id="c1",
                        rank=1,
                        reranker_score=2.4,
                        selected=True,
                    )
                ],
            ),
        ],
        selected_chunk_ids=["c1"],
        timings_ms=RetrievalTraceTimings(
            query_embedding=1.0,
            dense_retrieval=2.0,
            sparse_retrieval=3.0,
            rrf=0.1,
            rerank=4.0,
        ),
    )


def test_retrieve_only_disabled_keeps_legacy_shape_and_path(monkeypatch):
    rows = [_row()]

    monkeypatch.setattr(retriever, "retrieve", lambda question: rows)
    monkeypatch.setattr(
        retriever,
        "retrieve_with_trace",
        lambda question: (_ for _ in ()).throw(
            AssertionError("关闭 Trace 时不应调用 retrieve_with_trace")
        ),
    )

    result = adapters.retrieve_only("问题")

    assert result["retrieval"] == rows
    assert set(result) == {"retrieval", "retrieval_s"}


def test_retrieve_only_trace_is_explicit_serialized_and_does_not_change_rows(
    monkeypatch,
):
    rows = [_row()]
    response = RetrievalResponse(results=list(rows), trace=_trace())
    calls = []
    monkeypatch.setattr(settings, "RETRIEVAL_TRACE_ENABLED", False)
    monkeypatch.setattr(
        retriever,
        "retrieve_with_trace",
        lambda question: calls.append(settings.RETRIEVAL_TRACE_ENABLED)
        or response,
    )
    monkeypatch.setattr(retriever, "retrieve", lambda question: list(rows))

    legacy = adapters.retrieve_only("问题")
    traced = adapters.retrieve_only("问题", include_trace=True)

    assert legacy["retrieval"] == traced["retrieval"] == rows
    assert calls == [True]
    assert settings.RETRIEVAL_TRACE_ENABLED is False
    assert [stage["name"] for stage in traced["trace"]["stages"]] == [
        "dense",
        "bm25",
        "rrf",
        "reranker",
        "selected",
    ]
    dense = traced["trace"]["stages"][0]["candidates"]
    assert dense[1]["eligible"] is False
    assert dense[1]["filtered_reason"] == "distance_above_threshold"
    assert traced["trace"]["stages"][1]["candidates"][0]["bm25_score"] == 4.5
    assert traced["trace"]["stages"][2]["candidates"][0]["rrf_score"] == 0.031
    assert traced["trace"]["stages"][3]["candidates"][0]["reranker_score"] == 2.4
    assert traced["trace"]["selected_chunk_ids"] == ["c1"]
    assert traced["trace"]["timings_ms"]["total"] >= 0.0
    assert "followup_rewrite" not in traced["trace"]["timings_ms"]
    assert "citation_extraction" not in traced["trace"]["timings_ms"]
    assert "persistence" not in traced["trace"]["timings_ms"]
    assert "timing_contract_version" not in traced["trace"]
    assert traced["trace"]["settings_revision"] is None
    assert traced["trace"]["filtered_document_count"] is None


def test_retrieve_only_forwards_explicit_runtime_config_without_global_mutation(
    monkeypatch,
):
    rows = [_row()]
    runtime_config = object()
    calls = []
    response = RetrievalResponse(results=list(rows), trace=_trace())
    monkeypatch.setattr(settings, "RETRIEVAL_TRACE_ENABLED", False)
    monkeypatch.setattr(
        retriever,
        "retrieve",
        lambda question, **kwargs: calls.append(("plain", question, kwargs))
        or rows,
    )
    monkeypatch.setattr(
        retriever,
        "retrieve_with_trace",
        lambda question, **kwargs: calls.append(("trace", question, kwargs))
        or response,
    )

    plain = adapters.retrieve_only("问题", runtime_config=runtime_config)
    traced = adapters.retrieve_only(
        "问题",
        include_trace=True,
        runtime_config=runtime_config,
    )

    assert plain["retrieval"] == traced["retrieval"] == rows
    assert calls == [
        ("plain", "问题", {"runtime_config": runtime_config}),
        ("trace", "问题", {"runtime_config": runtime_config}),
    ]
    assert settings.RETRIEVAL_TRACE_ENABLED is False


def test_run_config_reads_loaded_cpu_models_and_cuda_peaks_without_loading(
    monkeypatch,
):
    class CachedGetter:
        def __init__(self, model):
            self.model = model
            self.calls = 0

        def cache_info(self):
            return SimpleNamespace(currsize=1)

        def __call__(self):
            self.calls += 1
            return self.model

    embedding_model = SimpleNamespace(device="cpu")
    reranker_model = SimpleNamespace(device="cpu")
    embedding_getter = CachedGetter(embedding_model)
    reranker_getter = CachedGetter(SimpleNamespace(model=reranker_model))
    monkeypatch.setattr(embedding, "_get_model", embedding_getter)
    monkeypatch.setattr(reranker, "_get_model", reranker_getter)
    monkeypatch.setattr(settings, "EMBEDDING_DEVICE", "cpu")
    monkeypatch.setattr(settings, "RERANKER_DEVICE", "cpu")

    config = adapters.get_run_config()
    runtime = config["runtime"]

    assert runtime["embedding"]["actual_device"] == "cpu"
    assert runtime["embedding"]["verified"] is True
    assert runtime["reranker"]["actual_device"] == "cpu"
    assert runtime["reranker"]["verified"] is True
    peaks = runtime["cuda_peak_memory"]
    assert set(peaks) == {"allocated_bytes", "reserved_bytes"}
    if config["torch_cuda_available"]:
        # CUDA availability is a process/runtime fact, independent of the two
        # mocked CPU model devices.  A GPU-enabled test process may already
        # have non-zero global peaks from earlier integration tests.
        assert peaks["allocated_bytes"] >= 0
        assert peaks["reserved_bytes"] >= peaks["allocated_bytes"]
    else:
        assert peaks == {"allocated_bytes": 0, "reserved_bytes": 0}
    assert embedding_getter.calls == 1
    assert reranker_getter.calls == 1


def test_retrieval_sweep_trace_flag_writes_per_question_trace_without_llm(
    monkeypatch, tmp_path
):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    dataset_path = tmp_path / "questions.json"
    dataset_path.write_text(
        json.dumps(
            {
                "version": 1,
                "questions": [
                    {
                        "id": "q1",
                        "question": "证据一是什么？",
                        "category": "fact",
                        "answerable": True,
                        "evidence": [
                            {
                                "document_name": "doc.txt",
                                "snippet": "证据一",
                                "start_char": 0,
                                "end_char": 3,
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls = []

    monkeypatch.setattr(eval_cli, "_setup_env", lambda args: run_dir)
    monkeypatch.setattr(eval_cli, "_validate_v3_gate", lambda *args, **kwargs: False)
    monkeypatch.setattr(eval_cli, "_validate_dataset", lambda *args: None)
    monkeypatch.setattr(adapters, "assert_isolation", lambda: None)
    monkeypatch.setattr(adapters, "parse_corpus", lambda corpus: {"doc.txt": "证据一"})
    monkeypatch.setattr(
        adapters,
        "ingest_corpus",
        lambda corpus: {
            "documents": {"doc.txt": 1},
            "total_chunks": 1,
            "ingest_s": 0.1,
        },
    )
    monkeypatch.setattr(
        adapters,
        "get_run_config",
        lambda: {"reranker_model": "fake", "runtime": {"embedding": {}, "reranker": {}}},
    )

    def fake_retrieve_only(question, *, include_trace=False):
        calls.append(include_trace)
        trace = _trace().model_dump(mode="json")
        trace["timings_ms"]["total"] = 4.0
        return {
            "retrieval": [_row()],
            "retrieval_s": 0.01,
            "trace": trace,
        }

    monkeypatch.setattr(adapters, "retrieve_only", fake_retrieve_only)
    args = SimpleNamespace(
        label="trace-sweep",
        dataset=str(dataset_path),
        corpus=str(tmp_path),
        candidate_ks=[15],
        retrieval_trace=True,
    )

    eval_cli.cmd_retrieval_sweep(args)

    raw = json.loads((run_dir / "retrieval-k15.jsonl").read_text("utf-8"))
    payload = json.loads((run_dir / "retrieval-sweep.json").read_text("utf-8"))
    assert calls == [True]
    assert [stage["name"] for stage in raw["trace"]["stages"]] == [
        "dense",
        "bm25",
        "rrf",
        "reranker",
        "selected",
    ]
    assert raw["trace"]["timings_ms"]["total"] == 4.0
    assert payload["config"]["retrieval_trace_enabled"] is True
    assert payload["retrieval_trace"]["per_question"] is True
    assert payload["candidates"][0]["trace_metrics"]["stages"]["dense"][
        "question_count"
    ] == 1
    assert payload["candidates"][0]["trace_metrics"]["timings_ms"]["total"][
        "mean"
    ] == 4.0
