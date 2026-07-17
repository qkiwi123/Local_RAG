"""
测试 backend/services/chunker.py — 文本切分器。

覆盖：
- 基础切分：块数量、每块长度、相邻块重叠
- 边界情况：空文本、短文本、纯空白
- 带 metadata 的切分：页码和文档名正确传递
"""

import pytest
from backend.services.chunker import _split_text, chunk_document


class TestSplitText:
    """底层滑动窗口切分函数测试。"""

    def test_basic_split(self):
        """正常长度文本切分，验证块数和块大小。"""
        text = "A" * 100
        chunks = _split_text(text, chunk_size=30, overlap=5)
        # 100 chars, chunk_size=30, step=25 → 4 chunks (30+25+25+20)
        assert len(chunks) == 4
        # 每块不超过 chunk_size
        for c in chunks:
            assert len(c) <= 30
        # 第一块长度应为 30
        assert len(chunks[0]) == 30

    def test_overlap_preserved(self):
        """相邻块之间应有重叠内容。"""
        text = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        chunks = _split_text(text, chunk_size=10, overlap=3)
        assert len(chunks) >= 2
        # 前一块的末尾应与后一块的开头有重叠
        for i in range(len(chunks) - 1):
            # 后一块的前 overlap 个字符应与前一块的末尾 overlap 个字符相同
            tail = chunks[i][-3:]  # 前一块末尾 3 个字符
            head = chunks[i + 1][:3]  # 后一块开头 3 个字符
            assert tail == head, f"Chunk {i} and {i+1} should overlap"

    def test_text_shorter_than_chunk_size(self):
        """文本长度小于 chunk_size，应直接返回一整块。"""
        text = "Short text"
        chunks = _split_text(text, chunk_size=500, overlap=50)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_empty_string(self):
        """空字符串应返回空列表。"""
        chunks = _split_text("", chunk_size=500, overlap=50)
        assert chunks == []

    def test_whitespace_only(self):
        """纯空白字符串应返回空列表。"""
        chunks = _split_text("   \n  \t  ", chunk_size=500, overlap=50)
        assert chunks == []


class TestChunkDocument:
    """带 metadata 的文档切分函数测试。"""

    def test_chunk_count(self):
        """验证切分后块数量符合预期。"""
        pages = [
            {"text": "A" * 100, "page_number": 1, "document_name": "test.pdf"},
            {"text": "B" * 80, "page_number": 2, "document_name": "test.pdf"},
        ]
        chunks = chunk_document(pages, chunk_size=30, overlap=5)
        # Page 1: 100 chars / step 25 → 4 chunks
        # Page 2: 80 chars / step 25 → 4 chunks
        # Total: 8
        assert len(chunks) == 8

    def test_page_number_preserved(self):
        """每块的页码应与来源页面一致，不跨页。"""
        pages = [
            {"text": "A" * 100, "page_number": 1, "document_name": "test.pdf"},
            {"text": "B" * 100, "page_number": 2, "document_name": "test.pdf"},
        ]
        chunks = chunk_document(pages, chunk_size=30, overlap=5)
        for chunk in chunks:
            assert chunk.metadata.page_number in [1, 2]
            # 验证内容来源页一致
            if chunk.metadata.page_number == 1:
                assert "A" in chunk.content
            else:
                assert "B" in chunk.content

    def test_document_name_preserved(self):
        """文档名应正确传递到每个块的 metadata 中。"""
        pages = [
            {"text": "Hello World", "page_number": 1, "document_name": "handbook.pdf"},
        ]
        chunks = chunk_document(pages, chunk_size=500, overlap=50)
        assert len(chunks) == 1
        assert chunks[0].metadata.document_name == "handbook.pdf"

    def test_document_id_preserved(self):
        pages = [
            {
                "text": "Hello World",
                "page_number": 1,
                "document_name": "handbook.pdf",
                "document_id": "stable-doc-id",
            },
        ]
        chunks = chunk_document(pages, chunk_size=500, overlap=50)
        assert chunks[0].metadata.document_id == "stable-doc-id"

    def test_chunk_index_sequential(self):
        """同一页内的 chunk_index 应连续递增。"""
        pages = [
            {"text": "A" * 200, "page_number": 1, "document_name": "test.pdf"},
        ]
        chunks = chunk_document(pages, chunk_size=30, overlap=5)
        for i, chunk in enumerate(chunks):
            assert chunk.metadata.chunk_index == i

    def test_empty_page_produces_no_chunks(self):
        """空文本页不产生任何块。"""
        pages = [
            {"text": "", "page_number": 1, "document_name": "test.pdf"},
            {"text": "   ", "page_number": 2, "document_name": "test.pdf"},
        ]
        chunks = chunk_document(pages, chunk_size=500, overlap=50)
        assert len(chunks) == 0
