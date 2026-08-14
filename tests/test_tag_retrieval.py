"""标签筛选下的 Dense/BM25/Hybrid 检索过滤测试（隔离 Chroma + 本地 embedding）。"""

import pytest

from backend.config import settings
from backend.models.schemas import ChunkMetadata, DocumentChunk
from backend.services.vector_store import add_chunks, delete_document_by_id

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def seeded_filter_store():
    def chunk(document_id, name, content):
        return DocumentChunk(
            content=content,
            metadata=ChunkMetadata(
                document_id=document_id,
                document_name=name,
                page_number=1,
                chunk_index=0,
                parser_version="structured-v1",
            ),
        )

    for document_id in ("tag-doc-a", "tag-doc-b", "tag-doc-c"):
        delete_document_by_id(document_id)
    add_chunks(
        [
            chunk(
                "tag-doc-a",
                "数据结构课件.pdf",
                "二叉树的前序遍历是先访问根节点再访问左右子树。",
            ),
            chunk(
                "tag-doc-b",
                "操作系统课件.pdf",
                "进程调度的常用算法包括先来先服务和时间片轮转。",
            ),
            chunk(
                "tag-doc-c",
                "数据库课件.pdf",
                "B+ 树是数据库索引的常见实现结构。",
            ),
        ]
    )
    yield
    for document_id in ("tag-doc-a", "tag-doc-b", "tag-doc-c"):
        delete_document_by_id(document_id)


class TestDenseFilter:
    def test_filter_limits_results_to_allowed_documents(self):
        from backend.services.vector_store import search

        rows = search(
            "树的遍历和索引结构",
            top_k=5,
            allowed_document_ids=["tag-doc-a", "tag-doc-c"],
        )
        assert rows
        assert {
            row["metadata"]["document_id"] for row in rows
        } <= {"tag-doc-a", "tag-doc-c"}

    def test_empty_allowed_list_returns_nothing(self):
        from backend.services.vector_store import search

        assert search("二叉树", top_k=5, allowed_document_ids=[]) == []

    def test_none_allowed_searches_everything(self):
        from backend.services.vector_store import search

        rows = search("二叉树", top_k=5, allowed_document_ids=None)
        assert any(
            row["metadata"]["document_id"] == "tag-doc-a" for row in rows
        )


class TestRetrieverFilterModes:
    def test_dense_mode_obeys_filter(self, monkeypatch):
        from backend.services import retriever

        monkeypatch.setattr(settings, "RETRIEVAL_MODE", "dense", raising=False)
        rows = retriever.retrieve(
            "树的遍历和索引结构",
            top_k=5,
            allowed_document_ids=["tag-doc-a"],
        )
        assert rows
        assert {
            row["metadata"]["document_id"] for row in rows
        } == {"tag-doc-a"}

    def test_bm25_mode_obeys_filter(self, monkeypatch):
        from backend.services import retriever

        monkeypatch.setattr(settings, "RETRIEVAL_MODE", "bm25", raising=False)
        # 筛选后语料 3 份、查询词只出现在 1 份（IDF 为正），命中目标文档。
        rows = retriever.retrieve(
            "二叉树",
            top_k=5,
            allowed_document_ids=["tag-doc-a", "tag-doc-b", "tag-doc-c"],
        )
        assert rows
        assert {
            row["metadata"]["document_id"] for row in rows
        } == {"tag-doc-a"}

        # 排除含查询词的文档后无结果。
        assert (
            retriever.retrieve(
                "二叉树",
                top_k=5,
                allowed_document_ids=["tag-doc-b", "tag-doc-c"],
            )
            == []
        )

    def test_hybrid_mode_obeys_filter(self, monkeypatch):
        from backend.services import retriever

        monkeypatch.setattr(settings, "RETRIEVAL_MODE", "hybrid", raising=False)
        rows = retriever.retrieve(
            "树的遍历和索引结构",
            top_k=5,
            allowed_document_ids=["tag-doc-a", "tag-doc-c"],
        )
        assert rows
        assert {
            row["metadata"]["document_id"] for row in rows
        } <= {"tag-doc-a", "tag-doc-c"}

    def test_filter_to_empty_range_short_circuits(self, monkeypatch):
        from backend.services import retriever

        for mode in ("dense", "bm25", "hybrid"):
            monkeypatch.setattr(settings, "RETRIEVAL_MODE", mode, raising=False)
            assert (
                retriever.retrieve(
                    "二叉树",
                    top_k=5,
                    allowed_document_ids=[],
                )
                == []
            )

    def test_trace_records_tag_filter(self, monkeypatch):
        from backend.services import retriever

        monkeypatch.setattr(
            settings, "RETRIEVAL_TRACE_ENABLED", True, raising=False
        )
        monkeypatch.setattr(settings, "RETRIEVAL_MODE", "dense", raising=False)
        response = retriever.retrieve_with_trace(
            "二叉树",
            top_k=3,
            allowed_document_ids=["tag-doc-a"],
            tag_filter=["课程课件/数据结构"],
        )

        assert response.trace is not None
        assert response.trace.tag_filter == ["课程课件/数据结构"]
        assert response.trace.filtered_document_count == 1
