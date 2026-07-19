"""P2-3 查询向量化与 Chroma 查询计时接缝测试。"""

from backend.services import vector_store


def test_search_by_embedding_skips_query_encoding(monkeypatch):
    captured = {}

    class FakeCollection:
        def query(self, **kwargs):
            captured.update(kwargs)
            return {
                "ids": [["c1"]],
                "documents": [["正文"]],
                "metadatas": [[{"document_name": "doc.txt", "page_number": 1}]],
                "distances": [[0.25]],
            }

    monkeypatch.setattr(vector_store, "_collection", FakeCollection())
    monkeypatch.setattr(
        vector_store,
        "embed_query",
        lambda query: (_ for _ in ()).throw(AssertionError("不应重复编码")),
    )

    rows = vector_store.search_by_embedding([0.1, 0.2], top_k=3)

    assert captured["query_embeddings"] == [[0.1, 0.2]]
    assert captured["n_results"] == 3
    assert rows[0]["chunk_id"] == "c1"
    assert rows[0]["distance"] == 0.25


def test_search_remains_a_compatible_wrapper(monkeypatch):
    monkeypatch.setattr(vector_store, "embed_query", lambda query: [0.3])
    monkeypatch.setattr(
        vector_store,
        "search_by_embedding",
        lambda embedding, top_k: [{"embedding": embedding, "top_k": top_k}],
    )

    assert vector_store.search("问题", top_k=4) == [
        {"embedding": [0.3], "top_k": 4}
    ]
