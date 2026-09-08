"""中文BM25内存兼容路径与P3持久化全库路径测试。"""

import pytest

from backend.services import sparse_retriever
from backend.services import bm25s_store
from backend.services.sparse_index import (
    SparseIndexDirtyError,
    SparseIndexMismatchError,
    SparseIndexMissingError,
)
from backend.services.sparse_tokenizer import tokenize as pure_tokenize


@pytest.fixture(autouse=True)
def _isolate_revision_binding(monkeypatch):
    monkeypatch.setattr(sparse_retriever, "_bound_revision", 0)
    sparse_retriever.clear_cache()
    yield
    sparse_retriever.clear_cache()


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
    assert sparse_retriever.tokenize is pure_tokenize


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


def _install_persistent_generation(monkeypatch, tmp_path):
    chunks = [
        _chunk("c1", "二叉树前序遍历"),
        _chunk("c2", "进程调度算法"),
        _chunk("c3", "数据库索引结构"),
    ]
    monkeypatch.setattr(sparse_retriever.settings, "SPARSE_BACKEND", "bm25s_mmap")
    monkeypatch.setattr(sparse_retriever.settings, "SPARSE_INDEX_ROOT", str(tmp_path))
    monkeypatch.setattr(
        sparse_retriever.settings, "SPARSE_INDEX_CORPUS_ID", "test-corpus"
    )
    monkeypatch.setattr(sparse_retriever.settings, "CHUNK_STRATEGY", "fixed")
    monkeypatch.setattr(sparse_retriever.settings, "CHUNK_SIZE", 500)
    monkeypatch.setattr(sparse_retriever.settings, "CHUNK_OVERLAP", 50)
    monkeypatch.setattr(sparse_retriever.settings, "CHUNK_MAX_TOKENS", 480)
    monkeypatch.setattr(sparse_retriever.settings, "BM25_K1", 1.5)
    monkeypatch.setattr(sparse_retriever.settings, "BM25_B", 0.75)
    bm25s_store.build_generation(
        chunks=chunks,
        target_root=tmp_path,
        corpus_id="test-corpus",
        tokenize=pure_tokenize,
        tokenizer={
            "contract": bm25s_store.TOKENIZER_CONTRACT,
            "jieba_version": sparse_retriever.jieba.__version__,
            "user_dictionary_sha256": None,
            "stopwords_sha256": None,
        },
        chunking={
            "parser_version": sparse_retriever.PARSER_VERSION,
            "strategy": "fixed",
            "size": 500,
            "overlap": 50,
            "max_tokens": 480,
        },
        source={"chroma_collection": "knowledge_base"},
        generation_id="test-generation",
        publish=True,
    )
    by_id = {row["chunk_id"]: row for row in chunks}
    monkeypatch.setattr(
        sparse_retriever.vector_store,
        "get_chunks_by_ids",
        lambda ids: [by_id[chunk_id] for chunk_id in reversed(ids)],
    )
    monkeypatch.setattr(
        sparse_retriever.vector_store, "get_index_revision", lambda: 0
    )
    monkeypatch.setattr(
        sparse_retriever.vector_store,
        "get_chunk_count",
        lambda: len(chunks),
    )
    sparse_retriever.clear_cache()
    return chunks


def test_persistent_full_corpus_uses_mmap_without_full_scan(monkeypatch, tmp_path):
    _install_persistent_generation(monkeypatch, tmp_path)
    monkeypatch.setattr(
        sparse_retriever.vector_store,
        "get_all_chunks",
        lambda: pytest.fail("持久化健康路径不得全库扫描"),
    )
    monkeypatch.setattr(
        sparse_retriever,
        "BM25Okapi",
        lambda *args, **kwargs: pytest.fail("不得构建内存BM25"),
    )
    diagnostics = {}

    rows = sparse_retriever.search("二叉树", 2, diagnostics=diagnostics)

    assert [row["chunk_id"] for row in rows] == ["c1"]
    assert diagnostics["sparse_backend"] == "bm25s_mmap"
    assert diagnostics["sparse_index_status"] == "ready"
    assert diagnostics["sparse_index_generation"] == "test-generation"
    assert diagnostics["sparse_manifest_hash"]
    assert diagnostics["sparse_index_load_ms"] >= 0
    assert sparse_retriever._persistent_cache["index"].is_mmap


def test_persistent_index_is_reused_after_first_query(monkeypatch, tmp_path):
    _install_persistent_generation(monkeypatch, tmp_path)
    first = {}
    second = {}

    sparse_retriever.search("二叉树", 2, diagnostics=first)
    sparse_retriever.search("数据库", 2, diagnostics=second)

    assert first["sparse_index_load_ms"] > 0
    assert second["sparse_index_load_ms"] == 0


def test_persistent_contract_mismatch_fails_explicitly(monkeypatch, tmp_path):
    _install_persistent_generation(monkeypatch, tmp_path)

    with pytest.raises(SparseIndexMismatchError, match="k1"):
        sparse_retriever.search("二叉树", 2, k1=1.2)


def test_persistent_process_revision_is_dirty(monkeypatch, tmp_path):
    _install_persistent_generation(monkeypatch, tmp_path)
    monkeypatch.setattr(
        sparse_retriever.vector_store, "get_index_revision", lambda: 1
    )

    with pytest.raises(SparseIndexDirtyError, match="已变化"):
        sparse_retriever.search("二叉树", 2)


def test_persistent_missing_chroma_chunk_is_mismatch(monkeypatch, tmp_path):
    _install_persistent_generation(monkeypatch, tmp_path)
    monkeypatch.setattr(
        sparse_retriever.vector_store, "get_chunks_by_ids", lambda ids: []
    )

    with pytest.raises(SparseIndexMismatchError, match="不在当前Chroma"):
        sparse_retriever.search("二叉树", 2)


def test_persistent_empty_scope_does_not_touch_any_index(monkeypatch, tmp_path):
    _install_persistent_generation(monkeypatch, tmp_path)
    sparse_retriever.clear_cache()
    monkeypatch.setattr(
        sparse_retriever, "_search_persistent", lambda *args, **kwargs: pytest.fail()
    )
    monkeypatch.setattr(
        sparse_retriever, "_get_index", lambda *args, **kwargs: pytest.fail()
    )

    assert sparse_retriever.search(
        "二叉树", 2, allowed_document_ids=[]
    ) == []


def test_persistent_nonempty_filter_keeps_memory_subcorpus(monkeypatch, tmp_path):
    chunks = _install_persistent_generation(monkeypatch, tmp_path)
    monkeypatch.setattr(
        sparse_retriever.vector_store, "get_all_chunks", lambda: chunks
    )
    monkeypatch.setattr(
        sparse_retriever,
        "_search_persistent",
        lambda *args, **kwargs: pytest.fail("过滤范围不能使用全库持久化索引"),
    )
    diagnostics = {}

    rows = sparse_retriever.search(
        "二叉树",
        2,
        allowed_document_ids=["doc-c1", "doc-c2", "doc-c3"],
        diagnostics=diagnostics,
    )

    assert [row["chunk_id"] for row in rows] == ["c1"]
    assert diagnostics["sparse_backend"] == "rank_bm25_memory"


def test_persistent_missing_index_never_falls_back_to_full_scan(monkeypatch, tmp_path):
    monkeypatch.setattr(sparse_retriever.settings, "SPARSE_BACKEND", "bm25s_mmap")
    monkeypatch.setattr(sparse_retriever.settings, "SPARSE_INDEX_ROOT", str(tmp_path))
    monkeypatch.setattr(
        sparse_retriever.settings, "SPARSE_INDEX_CORPUS_ID", "test-corpus"
    )
    monkeypatch.setattr(
        sparse_retriever.vector_store, "get_index_revision", lambda: 0
    )
    monkeypatch.setattr(
        sparse_retriever.vector_store,
        "get_all_chunks",
        lambda: pytest.fail("索引缺失不得静默回退全库内存构建"),
    )
    sparse_retriever.clear_cache()

    with pytest.raises(SparseIndexMissingError, match="active.json"):
        sparse_retriever.search("二叉树", 2)


def test_persistent_dirty_pointer_invalidates_loaded_cache(monkeypatch, tmp_path):
    _install_persistent_generation(monkeypatch, tmp_path)
    sparse_retriever.search("二叉树", 2)
    bm25s_store.mark_active_dirty(tmp_path, "corpus changed")

    with pytest.raises(SparseIndexDirtyError, match="corpus changed"):
        sparse_retriever.search("二叉树", 2)

    assert sparse_retriever._persistent_cache["index"] is None


def test_persistent_bound_revision_serves_post_rebuild_queries(
    monkeypatch, tmp_path
):
    """后台重建发布后绑定revision：同revision可查，再变更立刻失效。"""
    _install_persistent_generation(monkeypatch, tmp_path)
    monkeypatch.setattr(
        sparse_retriever.vector_store, "get_index_revision", lambda: 3
    )
    sparse_retriever.bind_process_revision(3)

    rows = sparse_retriever.search("二叉树", 2)
    assert [row["chunk_id"] for row in rows] == ["c1"]

    monkeypatch.setattr(
        sparse_retriever.vector_store, "get_index_revision", lambda: 4
    )
    with pytest.raises(SparseIndexDirtyError, match="已失效"):
        sparse_retriever.search("二叉树", 2)


def test_persistent_chunk_count_tripwire_detects_swapped_corpus(
    monkeypatch, tmp_path
):
    """换库后chunk数不一致：指针即使ready也显式报mismatch。"""
    _install_persistent_generation(monkeypatch, tmp_path)
    monkeypatch.setattr(
        sparse_retriever.vector_store, "get_chunk_count", lambda: 999
    )
    sparse_retriever.clear_cache()

    with pytest.raises(SparseIndexMismatchError, match="不一致"):
        sparse_retriever.search("二叉树", 2)

    status = sparse_retriever.get_runtime_status()
    assert status["sparse_index_status"] == "mismatch"


def test_sparse_runtime_status_reports_loaded_generation(monkeypatch, tmp_path):
    _install_persistent_generation(monkeypatch, tmp_path)

    status = sparse_retriever.get_runtime_status()

    assert status["sparse_backend"] == "bm25s_mmap"
    assert status["sparse_index_status"] == "ready"
    assert status["sparse_index_generation"] == "test-generation"
    assert status["sparse_manifest_hash"]


def test_sparse_runtime_status_maps_missing_without_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(sparse_retriever.settings, "SPARSE_BACKEND", "bm25s_mmap")
    monkeypatch.setattr(sparse_retriever.settings, "SPARSE_INDEX_ROOT", str(tmp_path))
    monkeypatch.setattr(
        sparse_retriever.settings, "SPARSE_INDEX_CORPUS_ID", "test-corpus"
    )
    monkeypatch.setattr(
        sparse_retriever.vector_store, "get_index_revision", lambda: 0
    )
    sparse_retriever.clear_cache()

    status = sparse_retriever.get_runtime_status()

    assert status["sparse_index_status"] == "missing"
    assert status["sparse_fallback_reason"] is None
