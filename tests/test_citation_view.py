"""P2-4 citation_view 单元测试。"""

import pytest
from backend.models.document_structure import SourceLocator
from backend.services.parsers.assembler import BlockDraft, assemble_unit_from_drafts
from frontend.citation_view import (
    PREVIEW_DIALOG_BEHAVIOR_HTML,
    build_highlight_html,
    build_structured_document_preview_html,
    build_structured_preview_html,
    render_preview_dialog_behavior,
)


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


class TestBuildStructuredPreviewHtml:
    def _unit(self):
        return assemble_unit_from_drafts(
            document_id="doc-1",
            document_name="guide.md",
            locator=SourceLocator(
                kind="section",
                unit_index=0,
                location_label="章节：部署",
                heading_path=["部署"],
            ),
            drafts=[
                BlockDraft(
                    block_type="heading",
                    text="<部署>",
                    heading_level=1,
                ),
                BlockDraft(
                    block_type="paragraph",
                    text='执行 <script>alert("x")</script>',
                ),
                BlockDraft(
                    block_type="list_item",
                    text="安装依赖",
                    list_level=1,
                ),
                BlockDraft(
                    block_type="table",
                    text="| 组件 | 状态 |\n| --- | --- |\n| RAG | 可用 |",
                    table_rows=[["组件", "状态"], ["RAG", "可用"]],
                ),
            ],
        )

    def test_renders_structure_escapes_all_text_and_highlights_intersection(self):
        unit = self._unit()
        paragraph = unit.blocks[1]
        start = paragraph.start_char + 3
        end = start + len("<script>")

        result = build_structured_preview_html(
            unit.canonical_text,
            unit.blocks,
            start,
            end,
        )

        assert "<h1>&lt;部署&gt;</h1>" in result
        assert "<script>" not in result
        assert "&lt;" in result
        assert result.count("<mark>") == len("<script>")
        assert 'style="margin-left:1.5em"' in result
        assert "<table>" in result
        assert "<th>组件</th>" in result
        assert "<td>RAG</td>" in result

    def test_table_cell_highlight_uses_canonical_offsets(self):
        unit = self._unit()
        table = unit.blocks[-1]
        start = unit.canonical_text.index("RAG", table.start_char)

        result = build_structured_preview_html(
            unit.canonical_text,
            unit.blocks,
            start,
            start + 3,
        )

        assert "<td><mark>R</mark><mark>A</mark><mark>G</mark></td>" in result

    def test_rejects_corrupted_block_payload(self):
        unit = self._unit()
        blocks = [block.model_dump() for block in unit.blocks]
        blocks[0]["text"] = "篡改"

        with pytest.raises(Exception, match="长度|canonical|offset|不一致"):
            build_structured_preview_html(
                unit.canonical_text,
                blocks,
                0,
                1,
            )

    def test_can_render_non_target_unit_without_highlight(self):
        unit = self._unit()

        result = build_structured_preview_html(
            unit.canonical_text,
            unit.blocks,
            None,
            None,
        )

        assert "<mark>" not in result
        assert "<h1>&lt;部署&gt;</h1>" in result

    def test_document_preview_keeps_all_units_and_highlights_only_target(self):
        first = self._unit()
        second = assemble_unit_from_drafts(
            document_id="doc-1",
            document_name="guide.md",
            locator=SourceLocator(
                kind="section",
                unit_index=1,
                location_label="章节：验收",
                heading_path=["验收"],
            ),
            drafts=[
                BlockDraft(
                    block_type="paragraph",
                    text="整份文档的后续内容也必须展示。",
                ),
            ],
        )
        start = first.canonical_text.index("安装依赖")
        units = [
            {
                "unit_id": first.unit_id,
                "location": first.locator.model_dump(),
                "canonical_text": first.canonical_text,
                "blocks": [block.model_dump() for block in first.blocks],
            },
            {
                "unit_id": second.unit_id,
                "location": second.locator.model_dump(),
                "canonical_text": second.canonical_text,
                "blocks": [block.model_dump() for block in second.blocks],
            },
        ]

        result = build_structured_document_preview_html(
            units,
            first.unit_id,
            start,
            start + len("安装依赖"),
        )

        assert "章节：部署" in result
        assert "章节：验收" in result
        assert "整份文档的后续内容也必须展示。" in result
        assert "<mark>" in result
        assert result.count("🎯") == 1
        assert result.count('data-citation-preview-target="true"') == 1


def test_preview_dialog_behavior_fixes_close_button_and_scrolls_to_highlight(
    monkeypatch,
):
    rendered = {}

    def fake_html(content, **kwargs):
        rendered["content"] = content
        rendered["kwargs"] = kwargs

    monkeypatch.setattr("frontend.citation_view.components.html", fake_html)

    render_preview_dialog_behavior()

    assert rendered["content"] == PREVIEW_DIALOG_BEHAVIOR_HTML
    assert rendered["kwargs"] == {"height": 0}
    assert "button[aria-label=\"Close\"]" in PREVIEW_DIALOG_BEHAVIOR_HTML
    assert "'position', 'fixed'" in PREVIEW_DIALOG_BEHAVIOR_HTML
    assert '[data-citation-preview-target="true"] mark' in PREVIEW_DIALOG_BEHAVIOR_HTML
    assert "dialog.scrollTo" in PREVIEW_DIALOG_BEHAVIOR_HTML
