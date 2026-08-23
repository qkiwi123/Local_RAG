"""P2-1 中文 BM25 检索测试。"""

from backend.services import sparse_retriever


def _chunk(chunk_id: str, content: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "content": content,
        "metadata": {
            "document_id": f"doc-{chunk_id}",
            "document_name": f"{chunk_id}.txt",
            "page_number": 1,
        },
    }


def _install_snapshot(monkeypatch, chunks, revision=1):
    monkeypatch.setattr(
        sparse_retriever.vector_store,
        "get_all_chunks",
        lambda: chunks,
    )
    monkeypatch.setattr(
        sparse_retriever.vector_store,
        "get_index_revision",
        lambda: revision,
    )
    sparse_retriever.clear_cache()


def test_tokenize_preserves_chinese_model_and_number_tokens():
    tokens = sparse_retriever.tokenize("Qwen 2.5 支持中文，压缩 30-50%。")

    assert "qwen" in tokens
    assert "2.5" in tokens
    assert "中文" in tokens
    assert any(token in {"30", "50", "30-50"} for token in tokens)
    assert all(token not in {"-", "%", "."} for token in tokens)


def test_bm25_ranks_exact_model_name_first(monkeypatch):
    _install_snapshot(
        monkeypatch,
        [
            _chunk("c1", "Claude Code、AutoGPT、CrewAI 是 Agent 框架"),
            _chunk("c2", "智能体可以规划任务和调用工具"),
            _chunk("c3", "向量数据库保存文档嵌入"),
        ],
    )

    results = sparse_retriever.search("CrewAI 框架", top_k=2)

    assert [row["chunk_id"] for row in results] == ["c1"]
    assert results[0]["bm25_score"] > 0
    assert results[0]["bm25_rank"] == 1
    assert results[0]["distance"] is None


def test_bm25_excludes_zero_score_results(monkeypatch):
    _install_snapshot(
        monkeypatch,
        [
            _chunk("c1", "RAG 检索增强生成"),
            _chunk("c2", "Transformer 自注意力"),
            _chunk("c3", "Agent 工具调用"),
        ],
    )

    assert sparse_retriever.search("带薪年假", top_k=5) == []


def test_bm25_returns_empty_for_empty_query_or_index(monkeypatch):
    _install_snapshot(monkeypatch, [])

    assert sparse_retriever.search("Qdrant", top_k=5) == []
    assert sparse_retriever.search("", top_k=5) == []
    assert sparse_retriever.search("Qdrant", top_k=0) == []


def test_bm25_rebuilds_when_vector_revision_changes(monkeypatch):
    state = {
        "revision": 1,
        "chunks": [
            _chunk("c1", "旧内容"),
            _chunk("c2", "其他内容"),
            _chunk("c3", "更多内容"),
        ],
    }
    monkeypatch.setattr(
        sparse_retriever.vector_store,
        "get_all_chunks",
        lambda: state["chunks"],
    )
    monkeypatch.setattr(
        sparse_retriever.vector_store,
        "get_index_revision",
        lambda: state["revision"],
    )
    sparse_retriever.clear_cache()

    assert sparse_retriever.search("Qdrant", 5) == []

    state.update(
        revision=2,
        chunks=[
            _chunk("c4", "新型号 Qdrant"),
            _chunk("c5", "向量数据库"),
            _chunk("c6", "本地开发"),
        ],
    )
    assert sparse_retriever.search("Qdrant", 5)[0]["chunk_id"] == "c4"


def test_bm25_filters_by_allowed_documents(monkeypatch):
    _install_snapshot(
        monkeypatch,
        [
            _chunk("c1", "二叉树前序遍历"),
            _chunk("c2", "进程调度算法"),
            _chunk("c3", "数据库索引结构"),
        ],
    )

    # 筛选后语料中查询词仍为少数（1/3），IDF 为正，命中 c1。
    rows = sparse_retriever.search(
        "二叉树", top_k=5, allowed_document_ids=["doc-c1", "doc-c2", "doc-c3"]
    )
    assert [row["chunk_id"] for row in rows] == ["c1"]

    # 排除含查询词的文档后无结果。
    rows = sparse_retriever.search(
        "二叉树", top_k=5, allowed_document_ids=["doc-c2", "doc-c3"]
    )
    assert rows == []

    # 不过滤时与全语料行为一致。
    rows = sparse_retriever.search("二叉树", top_k=5)
    assert [row["chunk_id"] for row in rows] == ["c1"]


def test_bm25_empty_allowed_list_returns_nothing(monkeypatch):
    _install_snapshot(monkeypatch, [_chunk("c1", "二叉树前序遍历")])

    assert sparse_retriever.search("二叉树", 5, allowed_document_ids=[]) == []


def test_bm25_filter_cache_is_keyed_by_allowed_set(monkeypatch):
    state = {"fetches": 0}
    chunks = [
        _chunk("c1", "二叉树前序遍历"),
        _chunk("c2", "进程调度算法"),
    ]

    def fake_all_chunks():
        state["fetches"] += 1
        return chunks

    monkeypatch.setattr(
        sparse_retriever.vector_store, "get_all_chunks", fake_all_chunks
    )
    monkeypatch.setattr(
        sparse_retriever.vector_store, "get_index_revision", lambda: 7
    )
    sparse_retriever.clear_cache()

    sparse_retriever.search("二叉树", 5, allowed_document_ids=["doc-c1"])
    sparse_retriever.search("二叉树", 5, allowed_document_ids=["doc-c1"])
    assert state["fetches"] == 1

    sparse_retriever.search("二叉树", 5, allowed_document_ids=["doc-c2"])
    assert state["fetches"] == 2

    sparse_retriever.search("二叉树", 5)
    assert state["fetches"] == 3


def test_bm25_cache_is_keyed_by_request_k1_and_b(monkeypatch):
    state = {"fetches": 0, "models": []}
    chunks = [_chunk("c1", "二叉树"), _chunk("c2", "进程调度")]

    class FakeBM25:
        def __init__(self, corpus, *, k1, b):
            state["models"].append((k1, b, len(corpus)))

        def get_scores(self, tokens):
            return [1.0, 0.0]

    monkeypatch.setattr(
        sparse_retriever.vector_store,
        "get_all_chunks",
        lambda: state.update(fetches=state["fetches"] + 1) or chunks,
    )
    monkeypatch.setattr(
        sparse_retriever.vector_store,
        "get_index_revision",
        lambda: 9,
    )
    monkeypatch.setattr(sparse_retriever, "BM25Okapi", FakeBM25)
    sparse_retriever.clear_cache()

    sparse_retriever.search("二叉树", 2, k1=1.2, b=0.6)
    sparse_retriever.search("二叉树", 2, k1=1.2, b=0.6)
    sparse_retriever.search("二叉树", 2, k1=1.9, b=0.6)

    assert state["fetches"] == 2
    assert state["models"] == [(1.2, 0.6, 2), (1.9, 0.6, 2)]
