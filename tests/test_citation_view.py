"""P2-4 citation_view 单元测试。"""

import pytest
from frontend.citation_view import build_highlight_html


class TestBuildHighlightHtml:
    def test_wraps_with_single_mark_tag(self):
        result = build_highlight_html("ABCDEFGHIJ", 2, 5)
        assert "<mark>CDE</mark>" in result
        # 只有一个 <mark>
        assert result.count("<mark>") == 1
        assert result.count("</mark>") == 1

    def test_entire_text_highlighted(self):
        result = build_highlight_html("ABCDEF", 0, 6)
        assert "<mark>ABCDEF</mark>" in result
        assert result.startswith('<pre')

    def test_pre_wrap_style_is_pre_wrap(self):
        result = build_highlight_html("ABC", 0, 1)
        assert 'white-space: pre-wrap' in result

    def test_chinese_and_markdown_table_preserved(self):
        text = "| 技术 | 成熟度 |\n| RAG | 成熟 |"
        result = build_highlight_html(text, 0, len(text))
        assert "| 技术 | 成熟度 |" in result
        assert "| RAG | 成熟 |" in result

    def test_html_escape_prevents_xss(self):
        result = build_highlight_html(
            '<script>alert("xss")</script>&"', 0, 30,
        )
        assert "<script>" not in result
        assert "&lt;script&gt;" in result
        assert "&amp;" in result
        assert "&quot;" in result

    def test_invalid_range_raises(self):
        with pytest.raises(Exception):
            build_highlight_html("ABCDEF", 10, 20)

    def test_negative_start_raises(self):
        with pytest.raises(Exception):
            build_highlight_html("ABCDEF", -1, 3)

    def test_end_not_greater_than_start_raises(self):
        with pytest.raises(Exception):
            build_highlight_html("ABCDEF", 3, 3)

    def test_empty_text_raises(self):
        with pytest.raises(Exception):
            build_highlight_html("", 0, 0)
