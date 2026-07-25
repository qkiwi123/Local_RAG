from backend.services.parsers import PARSER_VERSION
from backend.services.parsers import markdown as markdown_adapter


def test_markdown_h1_h2_create_sections_with_heading_paths(tmp_path):
    source = tmp_path / "guide.md"
    source.write_text(
        "前言。\n\n"
        "# 部署\n\n"
        "部署总览。\n\n"
        "## Windows\n\n"
        "Windows 步骤。\n\n"
        "### 环境变量\n\n"
        "配置 PATH。",
        encoding="utf-8",
    )

    units = markdown_adapter.parse_markdown_units(
        str(source),
        document_id="doc-md",
    )

    assert len(units) == 3
    assert [unit.locator.unit_index for unit in units] == [0, 1, 2]
    assert [unit.locator.heading_path for unit in units] == [
        [],
        ["部署"],
        ["部署", "Windows"],
    ]
    assert [unit.locator.location_label for unit in units] == [
        "第 1 节",
        "章节：部署",
        "章节：部署 / Windows",
    ]
    assert units[2].canonical_text == (
        "Windows\n\nWindows 步骤。\n\n环境变量\n\n配置 PATH。"
    )
    assert all(unit.parser_version == PARSER_VERSION for unit in units)


def test_markdown_preserves_supported_block_types_and_inline_meaning(tmp_path):
    source = tmp_path / "blocks.md"
    source.write_text(
        "# 标题\n\n"
        "阅读 [说明](https://example.com/guide) 和 ![架构图](arch.png)。\n\n"
        "- 一级\n"
        "  - 二级\n\n"
        "> 这是引用。\n\n"
        "```python\n"
        "print('ok')\n"
        "```\n\n"
        "| 组件 | 状态 |\n"
        "| --- | --- |\n"
        "| RAG | 可用 |\n",
        encoding="utf-8",
    )

    unit = markdown_adapter.parse_markdown_units(
        str(source),
        document_id="doc-blocks",
    )[0]

    assert [block.block_type for block in unit.blocks] == [
        "heading",
        "paragraph",
        "list_item",
        "list_item",
        "quote",
        "code",
        "table",
    ]
    assert "说明 (https://example.com/guide)" in unit.blocks[1].text
    assert "架构图" in unit.blocks[1].text
    assert "arch.png" not in unit.blocks[1].text
    assert [unit.blocks[2].list_level, unit.blocks[3].list_level] == [0, 1]
    assert unit.blocks[2].attributes["ordered"] is False
    assert unit.blocks[5].attributes["language"] == "python"
    assert unit.blocks[5].text == "print('ok')"
    assert unit.blocks[6].table_rows == [
        ["组件", "状态"],
        ["RAG", "可用"],
    ]
    assert unit.blocks[6].text == (
        "| 组件 | 状态 |\n| --- | --- |\n| RAG | 可用 |"
    )


def test_markdown_front_matter_is_not_indexed(tmp_path):
    source = tmp_path / "front-matter.md"
    source.write_text(
        "---\n"
        "title: 内部配置标题\n"
        "secret: not-content\n"
        "---\n"
        "# 对外标题\n\n"
        "正文。",
        encoding="utf-8",
    )

    unit = markdown_adapter.parse_markdown_units(
        str(source),
        document_id="doc-front",
    )[0]

    assert "secret" not in unit.canonical_text
    assert "内部配置标题" not in unit.canonical_text
    assert unit.canonical_text == "对外标题\n\n正文。"


def test_markdown_unheaded_content_uses_stable_size_limit(tmp_path, monkeypatch):
    source = tmp_path / "long.md"
    source.write_text("第一段 12345。\n\n第二段 67890。", encoding="utf-8")
    monkeypatch.setattr(markdown_adapter, "MAX_SECTION_CHARS", 10)

    first = markdown_adapter.parse_markdown_units(
        str(source),
        document_id="doc-long",
    )
    second = markdown_adapter.parse_markdown_units(
        str(source),
        document_id="doc-long",
    )

    assert len(first) == 2
    assert [unit.canonical_text for unit in first] == [
        "第一段 12345。",
        "第二段 67890。",
    ]
    assert [unit.unit_id for unit in first] == [unit.unit_id for unit in second]
