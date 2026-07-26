"""
测试 backend/services/retriever.py — 语义检索器。

覆盖：
- 相关查询能召回预期内容
- 完全不相关查询返回空列表（阈值过滤）
- 上下文格式化输出正确
"""

import pytest
from backend.services.retriever import retrieve, format_retrieval_context

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def seeded_retriever_store():
    """检索测试自行准备数据，不依赖 test_vector_store 的执行顺序。"""
    from backend.models.schemas import ChunkMetadata, DocumentChunk
    from backend.services.vector_store import add_chunks, delete_document

    delete_document("retriever_seed.pdf")
    chunks = [
        DocumentChunk(
            content=(
                f"Policy section {i}: employees receive annual leave and must follow "
                "company workplace guidelines."
            ),
            metadata=ChunkMetadata(
                document_id="retriever-seed",
                document_name="retriever_seed.pdf",
                page_number=1,
                chunk_index=i,
            ),
        )
        for i in range(10)
    ]
    add_chunks(chunks)
    yield
    delete_document("retriever_seed.pdf")


class TestRetrieve:
    """检索功能测试。"""

    def test_relevant_query_returns_results(self):
        """用相关查询检索，应返回非空结果。"""
        results = retrieve("annual leave policy", top_k=5)
        assert results

    def test_empty_query_returns_empty(self):
        """空查询应返回空列表。"""
        results = retrieve("", top_k=5)
        assert isinstance(results, list)

    def test_results_contain_required_fields(self):
        """每条结果应包含 content, metadata, distance 三个字段。"""
        results = retrieve("company policy", top_k=3)
        for r in results:
            assert "content" in r
            assert "metadata" in r
            assert "distance" in r
            assert "document_name" in r["metadata"]
            assert "page_number" in r["metadata"]

    def test_same_document_limit(self):
        """同一文档的结果不应超过 3 条。"""
        results = retrieve("company guidelines workplace conduct", top_k=8)
        doc_counts = {}
        for r in results:
            name = r["metadata"]["document_name"]
            doc_counts[name] = doc_counts.get(name, 0) + 1

        for doc_name, count in doc_counts.items():
            assert count <= 3, (
                f"Document '{doc_name}' has {count} results, "
                f"should be at most 3"
            )


    def test_top_k_respected(self):
        """返回结果数不超过请求的 top_k。"""
        results = retrieve("leave overtime sick", top_k=3)
        assert len(results) <= 3


class TestFormatRetrievalContext:
    """上下文格式化测试。"""

    def test_format_with_results(self):
        """有结果时应生成带来源标注的格式化文本。"""
        results = [
            {
                "content": "Employees are entitled to 5 days of paid annual leave.",
                "metadata": {
                    "document_name": "handbook.pdf",
                    "page_number": 3,
                },
                "distance": 0.2,
            },
            {
                "content": "Overtime is paid at 1.5x rate on weekdays.",
                "metadata": {
                    "document_name": "handbook.pdf",
                    "page_number": 5,
                },
                "distance": 0.3,
            },
        ]

        context = format_retrieval_context(results)

        # 应包含来源标注
        assert "【来源1】《handbook.pdf》第3页" in context
        assert "【来源2】《handbook.pdf》第5页" in context
        assert "《handbook.pdf》第3页" in context
        assert "《handbook.pdf》第5页" in context
        assert "Employees are entitled" in context
        assert "Overtime is paid" in context

    def test_format_empty_results(self):
        """空结果应返回提示文本。"""
        context = format_retrieval_context([])
        assert "未找到相关文档内容" in context

    def test_format_single_result(self):
        """单条结果也应正确格式化。"""
        results = [
            {
                "content": "Test content.",
                "metadata": {
                    "document_name": "test.pdf",
                    "page_number": 1,
                },
                "distance": 0.1,
            },
        ]
        context = format_retrieval_context(results)
        assert "【来源1】《test.pdf》第1页" in context
        assert "【来源2】" not in context  # 只有一条
        assert "Test content." in context

    def test_format_structured_result_uses_location_label(self):
        results = [
            {
                "content": "Structured content.",
                "metadata": {
                    "document_name": "slides.pptx",
                    "location_label": "第 8 张幻灯片",
                },
                "distance": 0.1,
            }
        ]

        context = format_retrieval_context(results)

        assert "【来源1】《slides.pptx》第 8 张幻灯片" in context
        assert "第?页" not in context
