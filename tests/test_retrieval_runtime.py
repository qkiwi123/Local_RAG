"""S4 请求级检索配置与各阶段候选深度解耦测试。"""

from backend.config import settings
from backend.models.settings_schemas import RetrievalSettings
from backend.services import retriever


def _row(chunk_id: str, *, distance: float = 0.1) -> dict:
    return {
        "chunk_id": chunk_id,
        "content": f"content-{chunk_id}",
        "metadata": {
            "document_id": f"doc-{chunk_id}",
            "document_name": f"{chunk_id}.txt",
            "page_number": 1,
        },
        "distance": distance,
    }


def _runtime(**overrides) -> RetrievalSettings:
    values = {
        "mode": "hybrid",
        "final_top_k": 3,
        "dense_candidate_k": 7,
        "dense_distance_threshold": 0.55,
        "bm25_top_k": 5,
        "bm25_k1": 1.8,
        "bm25_b": 0.6,
        "rrf_k": 45,
        "reranker_enabled": True,
        "reranker_candidate_k": 4,
        "max_chunks_per_document": 2,
    }
    values.update(overrides)
    return RetrievalSettings(**values)


def test_request_snapshot_drives_each_retrieval_stage_independently(monkeypatch):
    calls = {}

    def fake_dense(question, top_k, allowed_document_ids=None):
        calls["dense"] = (question, top_k, allowed_document_ids)
        return [_row(f"d{index}") for index in range(top_k)]

    def fake_bm25(
        question,
        top_k,
        allowed_document_ids=None,
        *,
        k1,
        b,
    ):
        calls["bm25"] = (question, top_k, allowed_document_ids, k1, b)
        return [
            {
                **_row(f"b{index}", distance=None),
                "bm25_rank": index + 1,
                "bm25_score": 10.0 - index,
            }
            for index in range(top_k)
        ]

    def fake_fusion(dense, sparse, rrf_k, top_k):
        calls["fusion"] = (len(dense), len(sparse), rrf_k, top_k)
        return dense + sparse

    def fake_rerank(question, rows):
        calls["reranker"] = (question, [row["chunk_id"] for row in rows])
        return rows

    monkeypatch.setattr(retriever, "vector_search", fake_dense)
    monkeypatch.setattr(retriever, "bm25_search", fake_bm25)
    monkeypatch.setattr(retriever, "reciprocal_rank_fusion", fake_fusion)
    monkeypatch.setattr(retriever, "rerank", fake_rerank)

    rows = retriever.retrieve("独立候选深度", runtime_config=_runtime())

    assert calls["dense"] == ("独立候选深度", 7, None)
    assert calls["bm25"] == ("独立候选深度", 5, None, 1.8, 0.6)
    assert calls["fusion"] == (7, 5, 45, 7)
    assert calls["reranker"] == (
        "独立候选深度",
        ["d0", "d1", "d2", "d3"],
    )
    assert [row["chunk_id"] for row in rows] == ["d0", "d1", "d2"]


def test_changing_final_top_k_does_not_change_upstream_candidate_depths(
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(
        retriever,
        "vector_search",
        lambda question, top_k, allowed_document_ids=None: (
            calls.append(("dense", top_k))
            or [_row(f"d{index}") for index in range(top_k)]
        ),
    )
    monkeypatch.setattr(
        retriever,
        "bm25_search",
        lambda question, top_k, allowed_document_ids=None, **kwargs: (
            calls.append(("bm25", top_k)) or []
        ),
    )
    monkeypatch.setattr(
        retriever,
        "reciprocal_rank_fusion",
        lambda dense, sparse, rrf_k, top_k: dense,
    )
    monkeypatch.setattr(
        retriever,
        "rerank",
        lambda question, rows: calls.append(("reranker", len(rows))) or rows,
    )

    first = retriever.retrieve("Q", runtime_config=_runtime(final_top_k=3))
    second = retriever.retrieve("Q", runtime_config=_runtime(final_top_k=2))

    assert calls == [
        ("dense", 7),
        ("bm25", 5),
        ("reranker", 4),
        ("dense", 7),
        ("bm25", 5),
        ("reranker", 4),
    ]
    assert len(first) == 3
    assert len(second) == 2


def test_hybrid_fusion_pool_can_supply_larger_reranker_depth(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        retriever,
        "vector_search",
        lambda question, top_k, allowed_document_ids=None: [
            _row(f"d{index}") for index in range(top_k)
        ],
    )
    monkeypatch.setattr(
        retriever,
        "bm25_search",
        lambda question, top_k, allowed_document_ids=None, **kwargs: [
            {
                **_row(f"b{index}", distance=None),
                "bm25_rank": index + 1,
                "bm25_score": 5.0,
            }
            for index in range(top_k)
        ],
    )

    def fake_fusion(dense, sparse, rrf_k, top_k):
        captured["fusion_top_k"] = top_k
        return (dense + sparse)[:top_k]

    monkeypatch.setattr(retriever, "reciprocal_rank_fusion", fake_fusion)
    monkeypatch.setattr(
        retriever,
        "rerank",
        lambda question, rows: captured.update(reranker_count=len(rows)) or rows,
    )

    retriever.retrieve(
        "供给深度",
        runtime_config=_runtime(
            final_top_k=3,
            dense_candidate_k=5,
            bm25_top_k=5,
            reranker_candidate_k=8,
        ),
    )

    assert captured == {"fusion_top_k": 8, "reranker_count": 8}


def test_request_threshold_filters_dense_and_is_visible_in_trace(monkeypatch):
    monkeypatch.setattr(settings, "RETRIEVAL_TRACE_ENABLED", True)
    monkeypatch.setattr(retriever, "embed_query", lambda question: [0.1])
    monkeypatch.setattr(
        retriever,
        "vector_search_by_embedding",
        lambda vector, top_k, allowed_document_ids=None: [
            _row("eligible", distance=0.2),
            _row("filtered", distance=0.4),
        ],
    )

    response = retriever.retrieve_with_trace(
        "阈值",
        runtime_config=_runtime(
            mode="dense",
            final_top_k=1,
            dense_candidate_k=2,
            dense_distance_threshold=0.3,
            bm25_top_k=1,
            reranker_enabled=False,
            reranker_candidate_k=1,
            max_chunks_per_document=1,
        ),
    )

    dense = response.trace.stages[0].candidates
    assert response.trace.retrieval_mode == "dense"
    assert response.trace.reranker_enabled is False
    assert response.trace.selected_chunk_ids == ["eligible"]
    assert dense[0].eligible is True
    assert dense[1].eligible is False
    assert dense[1].filtered_reason == "distance_above_threshold"


def test_request_max_chunks_per_document_limits_final_selection(monkeypatch):
    rows = [
        {
            **_row(f"same-{index}"),
            "metadata": {
                "document_id": "same-document",
                "document_name": "same.txt",
                "page_number": 1,
            },
        }
        for index in range(3)
    ] + [_row("other")]
    monkeypatch.setattr(
        retriever,
        "vector_search",
        lambda question, top_k, allowed_document_ids=None: rows,
    )

    selected = retriever.retrieve(
        "单文档上限",
        runtime_config=_runtime(
            mode="dense",
            final_top_k=3,
            dense_candidate_k=4,
            bm25_top_k=3,
            reranker_enabled=False,
            reranker_candidate_k=3,
            max_chunks_per_document=2,
        ),
    )

    assert [row["chunk_id"] for row in selected] == [
        "same-0",
        "same-1",
        "other",
    ]
