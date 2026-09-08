"""P6-A生命周期护栏测试：变更前持久标脏、标脏失败拒写、脏期间fail-closed。"""

from pathlib import Path

import pytest

from backend.services import bm25s_store, sparse_lifecycle, sparse_rebuild
from backend.services.sparse_index import (
    SparseIndexCorruptError,
    SparseIndexDirtyError,
)
from backend.services.sparse_tokenizer import tokenize as pure_tokenize


CHUNKS = [
    {"chunk_id": "c1", "content": "二叉树前序遍历", "metadata": {"document_id": "doc-c1"}},
    {"chunk_id": "c2", "content": "进程调度算法", "metadata": {"document_id": "doc-c2"}},
    {"chunk_id": "c3", "content": "数据库索引结构", "metadata": {"document_id": "doc-c3"}},
]


def _enable_mmap(monkeypatch, tmp_path: Path, *, corpus_id: str = "lifecycle-corpus") -> Path:
    """把部署配置切到隔离的bm25s_mmap测试根。"""
    from backend.config import settings

    root = tmp_path / "sparse_index"
    monkeypatch.setattr(settings, "SPARSE_BACKEND", "bm25s_mmap")
    monkeypatch.setattr(settings, "SPARSE_INDEX_ROOT", str(root))
    monkeypatch.setattr(settings, "SPARSE_INDEX_CORPUS_ID", corpus_id)
    monkeypatch.setattr(settings, "SPARSE_AUTO_REBUILD", True)
    monkeypatch.setattr(settings, "CHUNK_STRATEGY", "fixed")
    monkeypatch.setattr(settings, "CHUNK_SIZE", 500)
    monkeypatch.setattr(settings, "CHUNK_OVERLAP", 50)
    monkeypatch.setattr(settings, "CHUNK_MAX_TOKENS", 480)
    monkeypatch.setattr(settings, "BM25_K1", 1.5)
    monkeypatch.setattr(settings, "BM25_B", 0.75)
    return root


def _build_published(root: Path, chunks, generation_id: str) -> dict:
    from backend.config import settings
    from backend.services import sparse_retriever

    contract = sparse_retriever.expected_contract(
        k1=float(settings.BM25_K1), b=float(settings.BM25_B)
    )
    return bm25s_store.build_generation(
        chunks=chunks,
        target_root=root,
        corpus_id=contract["corpus_id"] or "isolated-lifecycle",
        tokenize=pure_tokenize,
        tokenizer=contract["tokenizer"],
        chunking=contract["chunking"],
        source={"chroma_collection": "knowledge_base"},
        generation_id=generation_id,
        publish=True,
    )


@pytest.fixture(autouse=True)
def _isolate_worker_state(monkeypatch):
    from backend.services import sparse_retriever, vector_store

    monkeypatch.setattr(sparse_retriever, "_bound_revision", 0)
    monkeypatch.setattr(vector_store, "_index_revision", 0)
    sparse_retriever.clear_cache()
    sparse_rebuild.reset_for_tests()
    sparse_retriever.clear_cache()
    yield
    sparse_rebuild.stop_worker(timeout=5.0)
    sparse_rebuild.reset_for_tests()


def test_mark_corpus_changing_is_noop_without_active_pointer(tmp_path):
    assert sparse_lifecycle.mark_corpus_changing("test", root=tmp_path) is None
    assert sparse_lifecycle.read_active_status(tmp_path) is None


def test_mark_corpus_changing_persists_dirty_and_is_idempotent(tmp_path):
    _build_published(tmp_path, CHUNKS, "gen-1")

    payload = sparse_lifecycle.mark_corpus_changing("upload:doc-1", root=tmp_path)
    assert payload["status"] == "dirty"
    assert payload["dirty_reason"] == "upload:doc-1"
    # 已dirty再标：幂等成功并刷新原因。
    payload = sparse_lifecycle.mark_corpus_changing("delete:doc-2", root=tmp_path)
    assert payload["status"] == "dirty"
    assert payload["dirty_reason"] == "delete:doc-2"
    assert sparse_lifecycle.read_active_status(tmp_path) == "dirty"


def test_mark_corpus_changing_rejects_corrupt_pointer(tmp_path):
    _build_published(tmp_path, CHUNKS, "gen-1")
    (tmp_path / "active.json").write_text("{broken", encoding="utf-8")

    with pytest.raises(SparseIndexCorruptError, match="不可读"):
        sparse_lifecycle.mark_corpus_changing("upload:doc-1", root=tmp_path)


def test_vector_store_add_marks_dirty_before_chroma_write(monkeypatch, tmp_path):
    """P6-A顺序不变量：先持久标脏，再写Chroma。"""
    from backend.services import vector_store

    _enable_mmap(monkeypatch, tmp_path)
    _build_published(tmp_path / "sparse_index", CHUNKS, "gen-1")
    order = []

    class RecordingCollection:
        def add(self, **kwargs):
            order.append("chroma_add")

        def delete(self, **kwargs):
            order.append("chroma_delete")

        def count(self):
            return 0

    monkeypatch.setattr(vector_store, "_collection", RecordingCollection())
    monkeypatch.setattr(vector_store, "embed_texts", lambda texts: [[0.0] * 4])
    real_mark = bm25s_store.mark_active_dirty

    def recording_mark(root, reason):
        order.append(f"mark_dirty:{reason}")
        return real_mark(root, reason)

    monkeypatch.setattr(bm25s_store, "mark_active_dirty", recording_mark)
    chunks = [
        vector_store_add_chunk("doc-9", "新文档内容"),
    ]
    assert vector_store.add_chunks(chunks) == 1
    assert order[0].startswith("mark_dirty:chroma_add")
    assert order[-1] == "chroma_add"
    assert sparse_lifecycle.read_active_status() == "dirty"


def vector_store_add_chunk(document_id: str, content: str):
    from backend.models.schemas import ChunkMetadata, DocumentChunk

    return DocumentChunk(
        content=content,
        metadata=ChunkMetadata(
            document_id=document_id,
            document_name=f"{document_id}.txt",
            page_number=1,
            chunk_index=0,
            parser_version="text-v1",
        ),
    )


def test_vector_store_add_rejected_when_mark_dirty_fails(monkeypatch, tmp_path):
    """标脏失败：Chroma不得写入，revision不得前进。"""
    from backend.services import vector_store

    _enable_mmap(monkeypatch, tmp_path)
    _build_published(tmp_path / "sparse_index", CHUNKS, "gen-1")
    written = []

    class FailingDiskCollection:
        def add(self, **kwargs):
            written.append(kwargs)
            raise AssertionError("标脏失败后不得写入Chroma")

        def count(self):
            return 0

    monkeypatch.setattr(vector_store, "_collection", FailingDiskCollection())
    monkeypatch.setattr(vector_store, "embed_texts", lambda texts: [[0.0] * 4])

    def failing_write(path, payload, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(bm25s_store, "_write_json", failing_write)
    with pytest.raises(OSError, match="disk full"):
        vector_store.add_chunks([vector_store_add_chunk("doc-9", "内容")])
    assert written == []
    # 指针保持ready（写入被拒绝，语料实际未变）。
    assert sparse_lifecycle.read_active_status() == "ready"


def test_vector_store_delete_rejected_when_mark_dirty_fails(monkeypatch, tmp_path):
    from backend.services import vector_store

    _enable_mmap(monkeypatch, tmp_path)
    _build_published(tmp_path / "sparse_index", CHUNKS, "gen-1")
    deleted = []

    class NoDeleteCollection:
        def delete(self, **kwargs):
            deleted.append(kwargs)
            raise AssertionError("标脏失败后不得删除Chroma")

        def count(self):
            return 3

    monkeypatch.setattr(vector_store, "_collection", NoDeleteCollection())

    def failing_write(path, payload, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(bm25s_store, "_write_json", failing_write)
    assert vector_store.delete_document_by_id("doc-c1") is False
    assert deleted == []


def test_dirty_status_survives_cache_clear_and_reload(monkeypatch, tmp_path):
    """重启语义：dirty在磁盘上，清空进程缓存后依然识别为dirty。"""
    from backend.services import sparse_retriever

    _enable_mmap(monkeypatch, tmp_path)
    root = tmp_path / "sparse_index"
    _build_published(root, CHUNKS, "gen-1")
    sparse_lifecycle.mark_corpus_changing("upload:doc-9", root=root)

    sparse_retriever.clear_cache()
    with pytest.raises(SparseIndexDirtyError, match="upload:doc-9"):
        sparse_retriever.search("二叉树", 2)
    status = sparse_retriever.get_runtime_status()
    assert status["sparse_index_status"] == "dirty"


def test_dirty_full_corpus_fails_closed_but_filter_and_dense_paths_survive(
    monkeypatch, tmp_path
):
    """脏期间：全库BM25显式失败；空范围短路；非空过滤与Dense仍可用。"""
    from backend.services import sparse_retriever, vector_store
    from backend.models.schemas import ChunkMetadata, DocumentChunk

    _enable_mmap(monkeypatch, tmp_path)
    _build_published(tmp_path / "sparse_index", CHUNKS, "gen-1")
    monkeypatch.setattr(
        vector_store,
        "get_all_chunks",
        lambda: list(CHUNKS),
    )
    monkeypatch.setattr(vector_store, "get_index_revision", lambda: 0)
    monkeypatch.setattr(vector_store, "get_chunk_count", lambda: 3)
    sparse_retriever.clear_cache()
    sparse_lifecycle.mark_corpus_changing("upload:doc-9", root=tmp_path / "sparse_index")

    # 全库：显式失败，不静默回退。
    with pytest.raises(SparseIndexDirtyError):
        sparse_retriever.search("二叉树", 2)
    # 空范围：不接触任何索引。
    assert sparse_retriever.search("二叉树", 2, allowed_document_ids=[]) == []
    # 非空过滤：精确子语料路径继续可用（3篇中1篇命中，IDF为正）。
    rows = sparse_retriever.search(
        "二叉树", 2, allowed_document_ids=["doc-c1", "doc-c2", "doc-c3"]
    )
    assert [row["chunk_id"] for row in rows] == ["c1"]

    # Dense模式不经过Sparse路径，检索仍然可用。
    from backend.services.retriever import RetrievalRuntimeConfig, retrieve

    real_chunk = DocumentChunk(
        content="二叉树前序遍历是深度优先策略",
        metadata=ChunkMetadata(
            document_id="doc-c1",
            document_name="c1.txt",
            page_number=1,
            chunk_index=0,
            parser_version="text-v1",
        ),
    )

    class FakeCollection:
        def query(self, **kwargs):
            return {
                "ids": [["doc-c1_p1_c0"]],
                "documents": [[real_chunk.content]],
                "metadatas": [[{
                    "document_id": "doc-c1",
                    "document_name": "c1.txt",
                    "page_number": 1,
                    "chunk_index": 0,
                    "parser_version": "text-v1",
                }]],
                "distances": [[0.1]],
            }

        def count(self):
            return 2

    monkeypatch.setattr(vector_store, "_collection", FakeCollection())
    monkeypatch.setattr(
        vector_store, "embed_query", lambda text: [0.0] * 4
    )
    config = RetrievalRuntimeConfig(
        mode="dense",
        final_top_k=2,
        dense_candidate_k=4,
        dense_distance_threshold=0.67,
        bm25_top_k=2,
        bm25_k1=1.5,
        bm25_b=0.75,
        rrf_k=60,
        reranker_enabled=False,
        reranker_candidate_k=2,
        max_chunks_per_document=2,
    )
    results = retrieve("二叉树", runtime_config=config)
    assert results and results[0]["metadata"]["document_id"] == "doc-c1"


def test_memory_backend_rollback_serves_queries_despite_dirty_pointer(
    monkeypatch, tmp_path
):
    """旧backend回滚：切回rank_bm25_memory后，即使指针dirty也可查询。"""
    from backend.config import settings
    from backend.services import sparse_retriever, vector_store

    _enable_mmap(monkeypatch, tmp_path)
    root = tmp_path / "sparse_index"
    _build_published(root, CHUNKS, "gen-1")
    sparse_lifecycle.mark_corpus_changing("upload:doc-9", root=root)

    monkeypatch.setattr(settings, "SPARSE_BACKEND", "rank_bm25_memory")
    monkeypatch.setattr(
        vector_store,
        "get_all_chunks",
        lambda: list(CHUNKS),
    )
    monkeypatch.setattr(vector_store, "get_index_revision", lambda: 0)
    sparse_retriever.clear_cache()

    rows = sparse_retriever.search("二叉树", 2)
    assert [row["chunk_id"] for row in rows] == ["c1"]
    status = sparse_retriever.get_runtime_status()
    assert status["sparse_backend"] == "rank_bm25_memory"


def test_chat_maps_sparse_index_error_to_explicit_503(monkeypatch):
    """全库Hybrid在脏期间的HTTP可见性：503而非笼统500。"""
    from fastapi.testclient import TestClient

    from backend.routers import chat as chat_router
    from backend.main import app as real_app

    # 复用真实应用但拦截检索入口，直接抛出Sparse索引错误。
    def failing_retrieve(*args, **kwargs):
        raise SparseIndexDirtyError("corpus changed")

    monkeypatch.setattr(chat_router, "retrieve_with_trace", failing_retrieve)
    with TestClient(real_app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/chat", json={"question": "二叉树是什么"}
        )
    assert response.status_code == 503
    assert "SparseIndexDirtyError" in response.json()["detail"]


def test_dirty_pointer_is_rejected_in_a_fresh_process(tmp_path):
    import subprocess
    import sys

    _build_published(tmp_path, CHUNKS, "restart-seed")
    sparse_lifecycle.mark_corpus_changing("interrupted_write", root=tmp_path)
    code = (
        "import sys\n"
        "from backend.services.bm25s_store import load_active\n"
        "from backend.services.sparse_index import SparseIndexDirtyError\n"
        "try:\n    load_active(sys.argv[1])\n"
        "except SparseIndexDirtyError:\n    print('dirty')\n"
        "else:\n    raise AssertionError('fresh process accepted dirty index')\n"
    )
    result = subprocess.run([sys.executable, "-B", "-c", code, str(tmp_path)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "dirty"


def _wait_for_outcome(predicate, timeout: float = 15.0) -> bool:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class TestUploadLifecycleIntegration:
    """真实Chroma + 伪造embedding的上传/删除/失败清理集成。"""

    @pytest.fixture()
    def api(self, monkeypatch, tmp_path):
        from backend.config import settings
        from backend.main import app
        from backend.services import vector_store

        root = _enable_mmap(monkeypatch, tmp_path)
        import chromadb
        from uuid import uuid4

        collection = chromadb.EphemeralClient().create_collection("p6a-" + uuid4().hex)
        monkeypatch.setattr(vector_store, "_collection", collection)
        monkeypatch.setattr(settings, "METADATA_DB_PATH", str(tmp_path / "metadata.db"))
        monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
        monkeypatch.setattr(
            vector_store, "embed_texts", lambda texts: [[0.0] * 4 for _ in texts]
        )
        monkeypatch.setattr(
            vector_store, "embed_query", lambda text: [0.0] * 4
        )
        sparse_rebuild.configure(backoff_s=0.05)
        # 模拟已经通过受控入口创建的活动代；禁止上传绕过首次发布门禁。
        _build_published(root, CHUNKS, "controlled-seed")
        from fastapi.testclient import TestClient

        with TestClient(app) as test_client:
            for name, text in (("trees.txt", b"Binary trees and traversal algorithms."),
                               ("database.txt", b"Database indexing and transaction storage.")):
                response = test_client.post("/api/upload", files={"file": (name, text, "text/plain")})
                assert response.status_code == 200, response.text
            assert sparse_rebuild.wait_for_idle(timeout=30)
            yield {"client": test_client, "root": root}

    def _upload(self, api, name: str):
        path = Path("tests/fixtures") / "sample.txt"
        with path.open("rb") as file_obj:
            return api["client"].post(
                "/api/upload",
                files={
                    "file": (
                        name,
                        file_obj,
                        "application/octet-stream",
                    )
                },
            )

    def test_upload_rebuilds_existing_controlled_generation(
        self, api, monkeypatch
    ):
        from backend.services import sparse_retriever, vector_store

        response = self._upload(api, "制度.txt")
        assert response.status_code == 200, response.text
        assert sparse_rebuild.wait_for_idle(timeout=30)
        status = sparse_rebuild.get_rebuild_status()
        assert status["last_result"]["outcome"] == "published"
        assert sparse_lifecycle.read_active_status(api["root"]) == "ready"
        # 进程内变更后由worker绑定revision，全库查询恢复。
        rows = sparse_retriever.search("annual leave", 3)
        assert rows, "重建发布后全库检索应恢复"
        assert vector_store.get_chunk_count() > 0

    def test_failed_ingest_cleanup_triggers_rebuild_and_stays_consistent(
        self, api, monkeypatch
    ):
        from backend.routers import upload as upload_router
        from backend.services import vector_store

        first = self._upload(api, "制度.txt")
        assert first.status_code == 200
        assert sparse_rebuild.wait_for_idle(timeout=30)

        # 第二份文档在标签写入阶段失败，触发清理补偿。
        def failing_tags(*args, **kwargs):
            raise RuntimeError("tag store unavailable")

        monkeypatch.setattr(upload_router, "set_document_tags", failing_tags)
        second = self._upload(api, "失效.txt")
        assert second.status_code == 500

        # 清理已回滚第二份文档的语料：回到与首份文档一致的内容摘要，
        # 协调器应复用现役generation并把dirty指针原子翻回ready。
        assert sparse_rebuild.wait_for_idle(timeout=30)
        status = sparse_rebuild.get_rebuild_status()
        assert status["last_result"]["outcome"] in {
            "published",
            "already_fresh",
        }
        assert sparse_lifecycle.read_active_status(api["root"]) == "ready"
        # 失败登记不会以ready状态存在。
        records = [
            record
            for record in _all_ready_documents()
            if record["original_name"] == "失效.txt"
        ]
        assert records == []
        # 注意：不得clear_cache——那会把进程绑定重置为0；worker刚发布的
        # 绑定正是"重建后恢复查询"的一部分。
        from backend.services import sparse_retriever

        rows = sparse_retriever.search("annual leave", 5)
        assert rows, "重建后全库检索应恢复"
        assert all(
            vector_store.get_chunk_by_id(row["chunk_id"]) for row in rows
        )

    def test_delete_triggers_rebuild_and_keeps_previous_generation(
        self, api
    ):
        from backend.services import sparse_retriever, vector_store

        first = self._upload(api, "制度.txt")
        assert first.status_code == 200
        assert sparse_rebuild.wait_for_idle(timeout=30)
        pointer = bm25s_store.read_active_payload(api["root"])
        generation_id_before = pointer["generation_id"]

        second = self._upload(api, "另一份.txt")
        assert second.status_code == 200
        assert sparse_rebuild.wait_for_idle(timeout=30)
        pointer = bm25s_store.read_active_payload(api["root"])
        assert pointer["status"] == "ready"

        response = api["client"].delete(
            f"/api/documents/id/{first.json()['document_id']}"
        )
        assert response.status_code == 200
        assert sparse_rebuild.wait_for_idle(timeout=30)
        status = sparse_rebuild.get_rebuild_status()
        assert status["last_result"]["outcome"] == "published"
        # 旧generation保留作为回滚候选。
        generations = list((api["root"] / "generations").iterdir())
        assert len(generations) >= 2
        assert generation_id_before != pointer["generation_id"]
        assert vector_store.get_chunk_count() > 0
        rows = sparse_retriever.search("annual leave", 5)
        assert all(
            row["metadata"]["document_id"] != first.json()["document_id"]
            for row in rows
        )


def _all_ready_documents():
    from backend.services.document_store import list_documents

    return list_documents(status="ready")
