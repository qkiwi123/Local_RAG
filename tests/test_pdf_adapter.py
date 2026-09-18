import pytest

from backend.config import settings
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


# ─── A/B 层 OCR 行为（mock 视觉调用，不访问网络） ───────────────────

def _image_pdf(path, pages=1):
    """用 Pillow 生成无文本层的图片型 PDF fixture。"""
    from PIL import Image

    first = Image.new("RGB", (120, 120), (255, 0, 0))
    if pages == 1:
        first.save(path, "PDF")
        return path
    rest = [Image.new("RGB", (120, 120), (0, 255, 0)) for _ in range(pages - 1)]
    first.save(path, "PDF", save_all=True, append_images=rest)
    return path


@pytest.fixture
def ocr_enabled(monkeypatch, temp_dir):
    from backend.services.parsers.ocr import clear_failure_memo

    clear_failure_memo()  # 用例间隔离进程级失败记忆
    monkeypatch.setattr(settings, "OCR_ENABLED", True)
    monkeypatch.setattr(settings, "OCR_PDF_SCANNED", True)
    monkeypatch.setattr(settings, "OCR_PDF_PAGE_IMAGES", False)
    monkeypatch.setattr(settings, "VISION_CACHE_DIR", f"{temp_dir}/vision_cache")
    monkeypatch.setattr(pdf_adapter, "render_page_image", lambda _p, _i: b"fake")
    return


def test_scanned_page_ocr_replaces_placeholder(monkeypatch, tmp_path, ocr_enabled):
    from backend.config import settings

    source = _image_pdf(tmp_path / "scanned.pdf")
    monkeypatch.setattr(
        pdf_adapter, "recognize_text", lambda image_bytes: "识别到的正文文字"
    )

    result = pdf_adapter.parse_pdf_document(
        str(source), document_id="doc-ocr"
    )

    assert result.warnings == []
    unit = result.units[0]
    assert unit.canonical_text == "识别到的正文文字"
    assert unit.blocks[0].attributes == {
        "ocr": True,
        "vision_model": settings.VISION_MODEL,
        "vision_kind": "scanned_page",
        "ocr_structure": "heuristic-v1",
    }
    assert unit.parser_version == PARSER_VERSION


def test_scanned_page_attaches_ocr_image_ref_when_preview_enabled(
    monkeypatch, tmp_path, ocr_enabled
):
    from backend.config import settings

    image_dir = tmp_path / "ocr_images"
    monkeypatch.setattr(settings, "OCR_IMAGE_PREVIEW_ENABLED", True)
    monkeypatch.setattr(settings, "OCR_IMAGE_DIR", str(image_dir))
    source = _image_pdf(tmp_path / "scanned.pdf")
    monkeypatch.setattr(
        pdf_adapter, "recognize_text", lambda image_bytes: "识别到的正文文字"
    )

    result = pdf_adapter.parse_pdf_document(
        str(source), document_id="doc-ocr-image"
    )

    attrs = result.units[0].blocks[0].attributes
    assert attrs["ocr_image_kind"] == "scanned_page"
    image_ref = attrs["ocr_image_ref"]
    assert isinstance(image_ref, str) and len(image_ref) == 16
    assert (image_dir / f"{image_ref}.jpg").read_bytes() == b"fake"


def test_ocr_disabled_keeps_placeholder(monkeypatch, tmp_path):
    from backend.config import settings

    source = _image_pdf(tmp_path / "scanned.pdf")
    monkeypatch.setattr(settings, "OCR_ENABLED", False)
    monkeypatch.setattr(
        pdf_adapter,
        "recognize_text",
        lambda image_bytes: (_ for _ in ()).throw(AssertionError("不应调用")),
    )

    result = pdf_adapter.parse_pdf_document(
        str(source), document_id="doc-off"
    )

    assert result.warnings == []
    assert result.units[0].canonical_text == "[第1页无可提取文本]"
    assert result.units[0].blocks[0].attributes == {}


def test_ocr_failure_falls_back_to_placeholder_with_warning(
    monkeypatch, tmp_path, ocr_enabled
):
    from backend.services.vision import VisionError

    source = _image_pdf(tmp_path / "scanned.pdf")
    monkeypatch.setattr(
        pdf_adapter,
        "recognize_text",
        lambda image_bytes: (_ for _ in ()).throw(VisionError("模型不可用")),
    )

    result = pdf_adapter.parse_pdf_document(
        str(source), document_id="doc-fail"
    )

    assert result.units[0].canonical_text == "[第1页视觉识别未完成：模型不可用]"
    assert len(result.warnings) == 1
    assert result.warnings[0].code == "ocr_page_failed"
    assert "模型不可用" in result.warnings[0].message


def test_ocr_budget_exceeded_keeps_later_pages_as_placeholder(
    monkeypatch, tmp_path, ocr_enabled
):
    from backend.config import settings

    source = _image_pdf(tmp_path / "scanned.pdf", pages=2)
    monkeypatch.setattr(settings, "OCR_MAX_PER_DOCUMENT", 1)
    monkeypatch.setattr(
        pdf_adapter, "recognize_text", lambda image_bytes: "识别文字"
    )

    result = pdf_adapter.parse_pdf_document(
        str(source), document_id="doc-budget"
    )

    assert result.units[0].canonical_text == "识别文字"
    assert "第2页视觉识别未完成" in result.units[1].canonical_text
    assert "单文档上限" in result.units[1].canonical_text
    assert len(result.warnings) == 1
    assert result.warnings[0].code == "ocr_page_failed"
    assert "上限" in result.warnings[0].message


def test_ocr_result_is_cached_across_parses(monkeypatch, tmp_path, ocr_enabled):
    source = _image_pdf(tmp_path / "scanned.pdf")
    calls = []

    def fake_recognize(image_bytes):
        calls.append(image_bytes)
        return "缓存文本"

    monkeypatch.setattr(pdf_adapter, "recognize_text", fake_recognize)

    first = pdf_adapter.parse_pdf_document(str(source), document_id="doc-cache")
    second = pdf_adapter.parse_pdf_document(str(source), document_id="doc-cache")

    assert first.units[0].canonical_text == "缓存文本"
    assert second.units[0].canonical_text == "缓存文本"
    assert len(calls) == 1


def test_page_images_layer_appends_description_block(
    monkeypatch, tmp_path, ocr_enabled
):
    from backend.config import settings

    source = tmp_path / "text-page.pdf"
    source.write_bytes(b"placeholder")
    _install_reader(monkeypatch, ["正文段落。"])
    monkeypatch.setattr(settings, "OCR_PDF_SCANNED", False)
    monkeypatch.setattr(settings, "OCR_PDF_PAGE_IMAGES", True)
    monkeypatch.setattr(pdf_adapter, "page_has_images", lambda _p, _i: True)
    monkeypatch.setattr(
        pdf_adapter, "describe_image", lambda image_bytes: "图表：柱状图展示销售额"
    )

    result = pdf_adapter.parse_pdf_document(
        str(source), document_id="doc-images"
    )

    unit = result.units[0]
    assert [block.text for block in unit.blocks] == [
        "正文段落。",
        "图表：柱状图展示销售额",
    ]
    assert unit.blocks[1].attributes == {
        "ocr": True,
        "vision_model": settings.VISION_MODEL,
        "vision_kind": "page_images",
    }
    assert result.warnings == []


def test_page_images_layer_skips_pages_without_images(
    monkeypatch, tmp_path, ocr_enabled
):
    from backend.config import settings

    source = tmp_path / "text-page.pdf"
    source.write_bytes(b"placeholder")
    _install_reader(monkeypatch, ["正文段落。"])
    monkeypatch.setattr(settings, "OCR_PDF_SCANNED", False)
    monkeypatch.setattr(settings, "OCR_PDF_PAGE_IMAGES", True)
    monkeypatch.setattr(pdf_adapter, "page_has_images", lambda _p, _i: False)
    monkeypatch.setattr(
        pdf_adapter,
        "describe_image",
        lambda image_bytes: (_ for _ in ()).throw(AssertionError("不应调用")),
    )

    result = pdf_adapter.parse_pdf_document(
        str(source), document_id="doc-no-images"
    )

    assert [block.text for block in result.units[0].blocks] == ["正文段落。"]


# ─── 畸形 PDF 的 pdfium 兜底 ───────────────────────────────────────

def test_reader_fallback_uses_pdfium_and_adds_warning(monkeypatch, tmp_path):
    from PyPDF2.errors import PdfReadError

    source = tmp_path / "broken.pdf"
    source.write_bytes(b"%PDF-broken")
    monkeypatch.setattr(
        pdf_adapter,
        "read_page_texts_pypdf2",
        lambda _p: (_ for _ in ()).throw(
            PdfReadError("Could not read Boolean object")
        ),
    )
    monkeypatch.setattr(
        pdf_adapter,
        "read_page_texts_pdfium",
        lambda _p: ["备用引擎提取的正文"],
    )

    result = pdf_adapter.parse_pdf_document(
        str(source), document_id="doc-fallback"
    )

    assert result.units[0].canonical_text == "备用引擎提取的正文"
    assert len(result.warnings) == 1
    assert result.warnings[0].code == "pdf_reader_fallback"
    assert "备用引擎" in result.warnings[0].message


def test_both_engines_fail_raises_clear_value_error(monkeypatch, tmp_path):
    from PyPDF2.errors import PdfReadError

    source = tmp_path / "broken.pdf"
    source.write_bytes(b"%PDF-broken")
    monkeypatch.setattr(
        pdf_adapter,
        "read_page_texts_pypdf2",
        lambda _p: (_ for _ in ()).throw(PdfReadError("坏 xref")),
    )
    monkeypatch.setattr(
        pdf_adapter,
        "read_page_texts_pdfium",
        lambda _p: (_ for _ in ()).throw(RuntimeError("pdfium 也失败")),
    )

    with pytest.raises(ValueError, match="PDF 无法解析"):
        pdf_adapter.parse_pdf_document(str(source), document_id="doc-both-fail")


def test_pypdf2_success_adds_no_fallback_warning(monkeypatch):
    _install_reader(monkeypatch, ["正常正文。"])

    result = pdf_adapter.parse_pdf_document(
        "/tmp/normal.pdf", document_id="doc-normal"
    )

    assert result.warnings == []
    assert result.units[0].canonical_text == "正常正文。"


def test_pdfium_engine_reads_real_pdf_fixture():
    texts = pdf_adapter.read_page_texts_pdfium("tests/fixtures/sample.pdf")

    assert texts
    assert any(text.strip() for text in texts)
