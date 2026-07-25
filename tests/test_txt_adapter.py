from backend.services.parsers import PARSER_VERSION
from backend.services.parsers.txt import LINES_PER_VIRTUAL_PAGE, parse_txt_units


def test_txt_normalizes_and_splits_paragraphs(tmp_path):
    source = tmp_path / "sample.txt"
    source.write_text(
        "ＡＩ\u3000技术\t应用\r\n\r\n\r\n第二段  ２０２６",
        encoding="utf-8",
    )

    unit = parse_txt_units(
        str(source),
        document_id="doc-1",
        document_name="资料.txt",
    )[0]

    assert unit.parser_version == PARSER_VERSION
    assert unit.document_name == "资料.txt"
    assert unit.locator.kind == "virtual_page"
    assert unit.locator.location_label == "逻辑页 1"
    assert [block.block_type for block in unit.blocks] == [
        "paragraph",
        "paragraph",
    ]
    assert unit.canonical_text == "AI 技术 应用\n\n第二段 2026"


def test_txt_uses_fixed_physical_line_windows_for_virtual_pages(tmp_path):
    source = tmp_path / "pages.txt"
    lines = [f"第 {index + 1} 行\n" for index in range(LINES_PER_VIRTUAL_PAGE + 1)]
    source.write_text("".join(lines), encoding="utf-8")

    units = parse_txt_units(str(source), document_id="doc-pages")

    assert len(units) == 2
    assert [unit.locator.page_number for unit in units] == [1, 2]
    assert units[0].canonical_text.startswith("第 1 行")
    assert units[0].canonical_text.endswith(f"第 {LINES_PER_VIRTUAL_PAGE} 行")
    assert units[1].canonical_text == f"第 {LINES_PER_VIRTUAL_PAGE + 1} 行"


def test_txt_blank_virtual_page_is_skipped_without_renumbering(tmp_path):
    source = tmp_path / "blank-page.txt"
    source.write_text(
        ("\n" * LINES_PER_VIRTUAL_PAGE) + "第三部分",
        encoding="utf-8",
    )

    units = parse_txt_units(str(source), document_id="doc-gap")

    assert len(units) == 1
    assert units[0].locator.page_number == 2
    assert units[0].locator.unit_index == 1


def test_txt_empty_file_returns_no_units(tmp_path):
    source = tmp_path / "empty.txt"
    source.write_text("", encoding="utf-8")

    assert parse_txt_units(str(source), document_id="doc-empty") == []


def test_txt_ids_are_deterministic(tmp_path):
    source = tmp_path / "stable.txt"
    source.write_text("第一段\n\n第二段", encoding="utf-8")

    first = parse_txt_units(str(source), document_id="stable-doc")
    second = parse_txt_units(str(source), document_id="stable-doc")

    assert [unit.unit_id for unit in first] == [unit.unit_id for unit in second]
    assert [
        [block.block_id for block in unit.blocks]
        for unit in first
    ] == [
        [block.block_id for block in unit.blocks]
        for unit in second
    ]
