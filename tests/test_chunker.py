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

    def test_prefers_recursive_chinese_semantic_boundaries(self):
        """优先按段落切分，其次句子和子句，不应在可用边界前硬截断。"""
        text = "第一段包含完整信息。\n\n第二段也包含完整信息。\n\n第三段结束。"

        chunks = _split_text(text, chunk_size=18, overlap=0)

        assert chunks == [
            "第一段包含完整信息。\n\n",
            "第二段也包含完整信息。\n\n",
            "第三段结束。",
        ]

    def test_merges_small_clause_units_until_target_size(self):
        """短子句应合并为有用 chunk，而不是每个逗号都生成一个碎片。"""
        text = "甲，乙，丙，丁，戊。"

        chunks = _split_text(text, chunk_size=8, overlap=0)

        assert chunks == ["甲，乙，丙，丁，", "戊。"]

    def test_token_limit_is_a_hard_ceiling(self):
        """即使字符数未超目标，也必须按 tokenizer 的真实计数继续切分。"""
        token_counter = lambda value: len(value) * 2 + 2
        text = "甲乙丙丁戊己庚辛壬癸"

        chunks = _split_text(
            text,
            chunk_size=20,
            overlap=2,
            max_tokens=10,
            token_counter=token_counter,
        )

        assert len(chunks) > 1
        assert all(token_counter(chunk) <= 10 for chunk in chunks)


class TestChunkDocument:
    """带 metadata 的文档切分函数测试。"""

    def test_chunk_count(self):
        """验证切分后块数量符合预期。"""
        pages = [
            {"text": "A" * 100, "page_number": 1, "document_name": "test.pdf"},
            {"text": "B" * 80, "page_number": 2, "document_name": "test.pdf"},
        ]
        chunks = chunk_document(pages, chunk_size=30, overlap=5)
        # Page 1: starts at 0/25/50/75 → 4 chunks
        # Page 2: 0/25/50 已完整覆盖 80 字符，不再生成全重复的 5 字符尾块
        assert len(chunks) == 7

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

    def test_content_type_preserved(self):
        pages = [
            {
                "text": "| 技术 | 成熟度 |\n| --- | --- |\n| RAG | 成熟 |",
                "page_number": 2,
                "document_name": "report.docx",
                "document_id": "stable-doc-id",
                "content_type": "table",
            },
        ]

        chunks = chunk_document(pages, chunk_size=500, overlap=50)

        assert chunks[0].metadata.content_type == "table"

    def test_chunk_index_is_unique_across_blocks_on_same_page(self):
        pages = [
            {
                "text": "表格前正文",
                "page_number": 1,
                "document_name": "report.docx",
                "content_type": "paragraph",
            },
            {
                "text": "| 技术 | 成熟度 |\n| --- | --- |\n| RAG | 成熟 |",
                "page_number": 1,
                "document_name": "report.docx",
                "content_type": "table",
            },
            {
                "text": "表格后正文",
                "page_number": 1,
                "document_name": "report.docx",
                "content_type": "paragraph",
            },
        ]

        chunks = chunk_document(pages, chunk_size=500, overlap=50)

        assert [chunk.metadata.chunk_index for chunk in chunks] == [0, 1, 2]

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

    def test_offsets_stats_and_stable_ids_are_recorded(self):
        """元数据应能把 chunk 精确映射回同页逻辑文本，并在重复运行时稳定。"""
        pages = [
            {
                "text": "表格前正文。",
                "page_number": 1,
                "document_name": "report.docx",
                "document_id": "doc-1",
                "content_type": "paragraph",
            },
            {
                "text": "| 技术 | 成熟度 |\n| --- | --- |\n| RAG | 成熟 |",
                "page_number": 1,
                "document_name": "report.docx",
                "document_id": "doc-1",
                "content_type": "table",
            },
        ]
        token_counter = lambda value: len(value) + 2

        first = chunk_document(
            pages,
            chunk_size=20,
            overlap=4,
            max_tokens=18,
            token_counter=token_counter,
        )
        second = chunk_document(
            pages,
            chunk_size=20,
            overlap=4,
            max_tokens=18,
            token_counter=token_counter,
        )
        logical_page = "\n\n".join(page["text"] for page in pages)

        assert [chunk.metadata.chunk_id for chunk in first] == [
            chunk.metadata.chunk_id for chunk in second
        ]
        assert len({chunk.metadata.chunk_id for chunk in first}) == len(first)
        assert {chunk.metadata.block_index for chunk in first} == {0, 1}
        for chunk in first:
            metadata = chunk.metadata
            assert logical_page[metadata.start_char:metadata.end_char] == chunk.content
            assert metadata.char_count == len(chunk.content)
            assert metadata.token_count == token_counter(chunk.content)
            assert metadata.token_count <= 18

    def test_invalid_chunk_parameters_fail_fast(self):
        pages = [{"text": "测试", "page_number": 1, "document_name": "test.txt"}]

        with pytest.raises(ValueError, match="overlap"):
            chunk_document(pages, chunk_size=10, overlap=10)
