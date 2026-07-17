"""
测试 backend/services/parser.py — 文档解析器。

覆盖：
- PDF 解析：正确提取页数与页码
- TXT 解析：正确返回文本
- DOCX 解析：正确返回文本
- 不支持的文件类型抛出 ValueError
- 空 PDF 页面标记处理
"""

import pytest
from backend.services.parser import parse_document


class TestParseDocument:
    """解析器核心功能测试。"""

    def test_parse_pdf_pages(self):
        """PDF 解析后得到的页数正确，页码连续递增。"""
        pages = parse_document("tests/fixtures/sample.pdf")
        assert len(pages) == 3, f"Expected 3 pages, got {len(pages)}"
        assert [p["page_number"] for p in pages] == [1, 2, 3]
        assert all(p["document_name"] == "sample.pdf" for p in pages)

    def test_parse_txt_returns_content(self):
        """TXT 解析能正常返回至少 1 页内容。"""
        pages = parse_document("tests/fixtures/sample.txt")
        assert len(pages) >= 1
        assert len(pages[0]["text"]) > 0
        assert pages[0]["page_number"] == 1

    def test_parse_docx_returns_content(self):
        """DOCX 解析能正常返回至少 1 页内容。"""
        pages = parse_document("tests/fixtures/sample.docx")
        assert len(pages) >= 1
        assert len(pages[0]["text"]) > 0
        assert pages[0]["page_number"] == 1

    def test_unsupported_format_raises_error(self):
        """不支持的文件类型抛出 ValueError。"""
        with pytest.raises(ValueError, match="不支持的文件格式"):
            parse_document("tests/fixtures/sample.png")

    def test_pdf_page_content_not_empty(self):
        """PDF 每页内容不应为空。"""
        pages = parse_document("tests/fixtures/sample.pdf")
        for page in pages:
            assert len(page["text"]) > 0, f"Page {page['page_number']} is empty"
            # 确保不是空页面占位标记
            assert "无可提取文本" not in page["text"]

    def test_custom_document_name(self):
        """传入自定义文档名时应覆盖从路径提取的文件名。"""
        pages = parse_document(
            "tests/fixtures/sample.txt",
            document_name="custom_name.txt"
        )
        assert all(p["document_name"] == "custom_name.txt" for p in pages)

    def test_document_id_is_preserved(self):
        pages = parse_document(
            "tests/fixtures/sample.txt",
            document_name="custom_name.txt",
            document_id="stable-doc-id",
        )
        assert all(p["document_id"] == "stable-doc-id" for p in pages)
