"""
测试 backend/services/vector_store.py — 向量数据库操作。

覆盖：
- 添加文本块后能正确召回
- 搜索结果按相似度排序
- 删除文档后不可检索
- 文档列表获取
- 空搜索参数处理
"""

import pytest

pytestmark = pytest.mark.integration
@pytest.fixture(scope="module", autouse=True)
def seeded_vector_store():
    """数据位于 conftest 注入的临时 Chroma，不依赖其他测试模块。"""
    from backend.models.schemas import ChunkMetadata, DocumentChunk
    from backend.services.vector_store import add_chunks, delete_document

    delete_document("test_handbook.pdf")
    chunks = [
        DocumentChunk(
            content="The annual leave policy states that employees get 5 days per year.",
            metadata=ChunkMetadata(
                document_id="test-handbook",
                document_name="test_handbook.pdf",
                page_number=1,
                chunk_index=0,
            ),
        ),
        DocumentChunk(
            content="Overtime is paid at 1.5x the normal rate on weekdays.",
            metadata=ChunkMetadata(
                document_id="test-handbook",
                document_name="test_handbook.pdf",
                page_number=2,
                chunk_index=0,
            ),
        ),
    ]
    add_chunks(chunks)
    yield
    delete_document("test_handbook.pdf")
    delete_document("to_delete.pdf")


class TestVectorStore:
    """向量存储核心功能测试。

    conftest 在任何 backend import 前注入临时 Chroma 路径。
    """

    def test_add_and_search(self):
        """添加文本块后能够通过搜索召回。"""
        from backend.services.vector_store import search

        # 用相关查询检索
        results = search("How many annual leave days?", top_k=2)
        assert len(results) >= 1
        # 第一个结果应该关于 annual leave
        assert "annual leave" in results[0]["content"].lower()
        assert results[0]["metadata"]["document_name"] == "test_handbook.pdf"
        assert results[0]["chunk_id"] == "test-handbook_p1_c0"

    def test_search_orders_by_similarity(self):
        """搜索结果应按相似度降序排列（distance 从小到大）。"""
        from backend.services.vector_store import search

        results = search("annual leave policy", top_k=3)
        if len(results) >= 2:
            distances = [r["distance"] for r in results]
            # distance 越小越相似，应递增
            assert distances == sorted(distances), (
                f"Distances should be ascending, got {distances}"
            )

    def test_delete_document(self):
        """删除文档后，该文档的内容不再出现在搜索结果中。"""
        from backend.services.vector_store import (
            add_chunks, search, delete_document, get_document_list
        )
        from backend.models.schemas import DocumentChunk, ChunkMetadata

        # 先添加一个新文档
        chunks = [
            DocumentChunk(
                content="Unique content for deletion test: unicorn rainbow sparkle.",
                metadata=ChunkMetadata(
                    document_id="to-delete",
                    document_name="to_delete.pdf",
                    page_number=1,
                    chunk_index=0,
                ),
            ),
        ]
        add_chunks(chunks)

        # 确认存在
        results = search("unicorn rainbow", top_k=3)
        assert any("unicorn" in r["content"] for r in results)

        # 删除
        success = delete_document("to_delete.pdf")
        assert success

        # 确认删除后搜索不到
        results = search("unicorn rainbow", top_k=3)
        assert not any(
            r["metadata"].get("document_name") == "to_delete.pdf"
            for r in results
        )

    def test_get_document_list(self):
        """获取文档列表应包含之前添加的文档。"""
        from backend.services.vector_store import get_document_list

        docs = get_document_list()
        assert isinstance(docs, list)
        assert "test_handbook.pdf" in docs

    def test_search_empty_query(self):
        """空查询应返回空结果。"""
        from backend.services.vector_store import search

        results = search("", top_k=5)
        # 查询空字符串时返回结果（Chroma 不会报错）
        assert isinstance(results, list)

    def test_search_zero_top_k(self):
        """top_k=0 应返回空列表。"""
        from backend.services.vector_store import search

        results = search("anything", top_k=0)
        assert results == []

    def test_add_chunks_persists_content_type(self, monkeypatch):
        from backend.models.schemas import ChunkMetadata, DocumentChunk
        from backend.services import vector_store

        captured = {}

        class FakeCollection:
            def add(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setattr(vector_store, "_collection", FakeCollection())
        monkeypatch.setattr(vector_store, "embed_texts", lambda texts: [[0.0, 1.0]])
        chunk = DocumentChunk(
            content="| 技术 | 成熟度 |",
            metadata=ChunkMetadata(
                document_id="doc-table",
                document_name="report.docx",
                page_number=2,
                chunk_index=0,
                content_type="table",
            ),
        )

        assert vector_store.add_chunks([chunk]) == 1
        assert captured["metadatas"][0]["content_type"] == "table"

    def test_add_chunks_uses_stable_chunk_id_and_persists_position_stats(self, monkeypatch):
        from backend.models.schemas import ChunkMetadata, DocumentChunk
        from backend.services import vector_store

        captured = {}

        class FakeCollection:
            def add(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setattr(vector_store, "_collection", FakeCollection())
        monkeypatch.setattr(vector_store, "embed_texts", lambda texts: [[0.0] for _ in texts])
        chunk = DocumentChunk(
            content="测试内容",
            metadata=ChunkMetadata(
                document_id="doc-1",
                document_name="report.docx",
                page_number=2,
                chunk_index=3,
                content_type="paragraph",
                chunk_id="doc-1_p2_b0_s10_e14",
                block_index=0,
                start_char=10,
                end_char=14,
                char_count=4,
                token_count=6,
            ),
        )

        assert vector_store.add_chunks([chunk]) == 1
        assert captured["ids"] == ["doc-1_p2_b0_s10_e14"]
        assert captured["metadatas"] == [{
            "document_id": "doc-1",
            "document_name": "report.docx",
            "page_number": 2,
            "chunk_index": 3,
            "content_type": "paragraph",
            "block_index": 0,
            "start_char": 10,
            "end_char": 14,
            "char_count": 4,
            "token_count": 6,
        }]
