"""P2-1 三种检索模式与 Reranker 接缝测试。"""

from backend.config import settings
from backend.services import retriever


def _dense(chunk_id: str, distance: float, document_name=None) -> dict:
    return {
        "chunk_id": chunk_id,
        "content": f"dense-{chunk_id}",
        "metadata": {
            "document_id": document_name or f"doc-{chunk_id}",
            "document_name": document_name or f"{chunk_id}.txt",
            "page_number": 1,
        },
        "distance": distance,
    }


def _sparse(chunk_id: str, score: float, document_name=None) -> dict:
    return {
        "chunk_id": chunk_id,
        "content": f"sparse-{chunk_id}",
        "metadata": {
            "document_id": document_name or f"doc-{chunk_id}",
            "document_name": document_name or f"{chunk_id}.txt",
            "page_number": 1,
        },
        "distance": None,
        "bm25_score": score,
        "bm25_rank": 1,
    }


def test_hybrid_mode_fuses_before_reranking(monkeypatch):
    calls = {"dense": [], "bm25": [], "fusion": [], "rerank": []}
    monkeypatch.setattr(settings, "RETRIEVAL_MODE", "hybrid")
    monkeypatch.setattr(settings, "RERANKER_ENABLED", True)
    monkeypatch.setattr(settings, "RERANKER_CANDIDATE_K", 15)
    monkeypatch.setattr(settings, "BM25_TOP_K", 15)
    monkeypatch.setattr(settings, "RRF_K", 60)
    monkeypatch.setattr(settings, "RETRIEVAL_DISTANCE_THRESHOLD", 0.67)

    def fake_dense(question, top_k, allowed_document_ids=None):
        calls["dense"].append((question, top_k, allowed_document_ids))
        return [_dense("d1", 0.2)]

    def fake_bm25(question, top_k, allowed_document_ids=None, **kwargs):
        calls["bm25"].append((question, top_k, allowed_document_ids))
        return [_sparse("s1", 2.0)]

    def fake_fusion(dense, sparse, rrf_k, top_k):
        calls["fusion"].append((dense, sparse, rrf_k, top_k))
        return dense + sparse

    def fake_rerank(question, rows):
        calls["rerank"].append((question, rows))
        return list(reversed(rows))

    monkeypatch.setattr(retriever, "vector_search", fake_dense)
    monkeypatch.setattr(retriever, "bm25_search", fake_bm25, raising=False)
    monkeypatch.setattr(
        retriever,
        "reciprocal_rank_fusion",
        fake_fusion,
        raising=False,
    )
    monkeypatch.setattr(retriever, "rerank", fake_rerank)

    rows = retriever.retrieve("测试", top_k=2)

    assert calls["dense"] == [("测试", 15, None)]
    assert calls["bm25"] == [("测试", 15, None)]
    assert calls["fusion"][0][2:] == (60, 15)
    assert [row["chunk_id"] for row in calls["rerank"][0][1]] == ["d1", "s1"]
    assert [row["chunk_id"] for row in rows] == ["s1", "d1"]


def test_bm25_mode_does_not_call_dense(monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "RETRIEVAL_MODE", "bm25")
    monkeypatch.setattr(settings, "RERANKER_ENABLED", False)
    monkeypatch.setattr(settings, "BM25_TOP_K", 15)
    monkeypatch.setattr(
        retriever,
        "vector_search",
        lambda *args: (_ for _ in ()).throw(AssertionError("不应调用 Dense")),
    )

    def fake_bm25(question, top_k, allowed_document_ids=None, **kwargs):
        calls.append((question, top_k, allowed_document_ids))
        return [_sparse("s1", 1.0)]

    monkeypatch.setattr(retriever, "bm25_search", fake_bm25, raising=False)

    rows = retriever.retrieve("Qdrant", top_k=2)

    assert calls == [("Qdrant", 15, None)]
    assert [row["chunk_id"] for row in rows] == ["s1"]


def test_hybrid_keeps_sparse_candidate_when_dense_threshold_filters_all(
    monkeypatch,
):
    monkeypatch.setattr(settings, "RETRIEVAL_MODE", "hybrid")
    monkeypatch.setattr(settings, "RERANKER_ENABLED", False)
    monkeypatch.setattr(settings, "BM25_TOP_K", 15)
    monkeypatch.setattr(settings, "RRF_K", 60)
    monkeypatch.setattr(settings, "RETRIEVAL_DISTANCE_THRESHOLD", 0.67)
    monkeypatch.setattr(
        retriever,
        "vector_search",
        lambda question, top_k, allowed_document_ids=None, **kwargs: [
            _dense("filtered", 0.8)
        ],
    )
    monkeypatch.setattr(
        retriever,
        "bm25_search",
        lambda question, top_k, allowed_document_ids=None, **kwargs: [
            _sparse("kept", 3.0)
        ],
        raising=False,
    )

    rows = retriever.retrieve("RBAC", top_k=2)

    assert [row["chunk_id"] for row in rows] == ["kept"]
    assert rows[0]["distance"] is None
    assert rows[0]["rrf_score"] > 0
