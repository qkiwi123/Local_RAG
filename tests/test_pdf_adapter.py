from backend.models.document_structure import ParsedUnit
from backend.services.parsers import PARSER_VERSION
from backend.services.parsers import pdf as pdf_adapter


class _FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self):
        return self._text


class _FakeReader:
    def __init__(self, pages):
        self.pages = [_FakePage(text) for text in pages]


def _install_reader(monkeypatch, pages):
    monkeypatch.setattr(
        pdf_adapter,
        "PdfReader",
        lambda _file_path: _FakeReader(pages),
    )


def test_pdf_page_becomes_structured_unit_with_paragraph_blocks(monkeypatch):
    _install_reader(
        monkeypatch,
        ["第一段。\n\n第二段。"],
    )

    units = pdf_adapter.parse_pdf_units(
        "example.pdf",
        document_id="doc-1",
        document_name="报告.pdf",
    )

    assert len(units) == 1
    unit = units[0]
    assert isinstance(unit, ParsedUnit)
    assert unit.document_id == "doc-1"
    assert unit.document_name == "报告.pdf"
    assert unit.parser_version == PARSER_VERSION
    assert unit.locator.kind == "page"
    assert unit.locator.page_number == 1
    assert unit.locator.unit_index == 0
    assert unit.locator.location_label == "第 1 页"
    assert [block.block_type for block in unit.blocks] == [
        "paragraph",
        "paragraph",
    ]
    assert [block.text for block in unit.blocks] == ["第一段。", "第二段。"]
    assert unit.canonical_text == "第一段。\n\n第二段。"
    for block in unit.blocks:
        assert unit.canonical_text[block.start_char:block.end_char] == block.text


def test_pdf_empty_page_is_kept_as_explicit_placeholder(monkeypatch):
    _install_reader(monkeypatch, [None, "   "])

    units = pdf_adapter.parse_pdf_units(
        "empty-pages.pdf",
        document_id="doc-empty",
    )

    assert len(units) == 2
    assert [unit.locator.page_number for unit in units] == [1, 2]
    assert units[0].canonical_text == "[第1页无可提取文本]"
    assert units[1].canonical_text == "[第2页无可提取文本]"


def test_pdf_adapter_normalizes_text_before_block_creation(monkeypatch):
    _install_reader(
        monkeypatch,
        ["ＡＩ\u3000技术\t应用\r\n\r\n\r\n第二段  ２０２６"],
    )

    unit = pdf_adapter.parse_pdf_units(
        "normalized.pdf",
        document_id="doc-normalized",
    )[0]

    assert unit.canonical_text == "AI 技术 应用\n\n第二段 2026"
    assert [block.text for block in unit.blocks] == [
        "AI 技术 应用",
        "第二段 2026",
    ]


def test_pdf_ids_are_deterministic_across_repeated_parses(monkeypatch):
    pages = ["第一页正文。", "第二页正文。"]
    _install_reader(monkeypatch, pages)
    first = pdf_adapter.parse_pdf_units(
        "stable.pdf",
        document_id="stable-doc",
    )

    _install_reader(monkeypatch, pages)
    second = pdf_adapter.parse_pdf_units(
        "stable.pdf",
        document_id="stable-doc",
    )

    assert [unit.unit_id for unit in first] == [unit.unit_id for unit in second]
    assert [
        [block.block_id for block in unit.blocks]
        for unit in first
    ] == [
        [block.block_id for block in unit.blocks]
        for unit in second
    ]
    assert first[0].unit_id != first[1].unit_id


def test_pdf_document_name_defaults_to_source_filename(monkeypatch):
    _install_reader(monkeypatch, ["正文"])

    unit = pdf_adapter.parse_pdf_units(
        "/tmp/source-name.pdf",
        document_id="doc-name",
    )[0]

    assert unit.document_name == "source-name.pdf"
