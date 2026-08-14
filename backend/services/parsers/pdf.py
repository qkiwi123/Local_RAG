"""PDF adapter: PyPDF2 + 可选多模态 OCR -> ParsedDocument。

- A 层（OCR_PDF_SCANNED）：无文本层页面整页渲染后识别文字，替代
  「无可提取文本」占位符；
- B 层（OCR_PDF_PAGE_IMAGES）：有文本且含图片/图表的页面整页渲染，
  追加图片/图表说明块；
- 视觉产出块带 attributes={"ocr": True, ...}，预览可明示「AI 识别」；
- OCR 失败/超预算只产生 warning，不阻塞入库；关闭开关时输出与 v1 一致。
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PyPDF2 import PdfReader
from PyPDF2.errors import PyPdfError

from backend.config import settings
from backend.models.document_structure import (
    ParsedDocument,
    ParsedUnit,
    ParseWarning,
    SourceLocator,
)
from backend.services.parsers.assembler import BlockDraft, assemble_unit_from_drafts
from backend.services.parsers.ocr import (
    file_sha256,
    ocr_attributes,
    run_vision,
)
from backend.services.parsers.text import normalize_text, split_paragraphs
from backend.services.vision import describe_image, recognize_text
from backend.services.vision_cache import VisionBudget

_EMPTY_PAGE_TEMPLATE = "[第{page}页无可提取文本]"
_PDFIUM_IMAGE_TYPE = None  # 惰性从 pypdfium2 解析，兼容不同版本常量位置


def _scanned_ocr_enabled() -> bool:
    return settings.OCR_ENABLED and settings.OCR_PDF_SCANNED


def _page_images_enabled() -> bool:
    return settings.OCR_ENABLED and settings.OCR_PDF_PAGE_IMAGES


def read_page_texts_pypdf2(file_path: str) -> list[str]:
    """PyPDF2 提取各页文本；对畸形 xref 等结构会抛 PyPDF2Error。"""
    reader = PdfReader(file_path)
    return [
        normalize_text(page.extract_text() or "") for page in reader.pages
    ]


def read_page_texts_pdfium(file_path: str) -> list[str]:
    """pdfium（Chrome 同款引擎）提取各页文本，容忍多数真实世界的畸形 PDF。"""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(file_path)
    try:
        texts = []
        for page in pdf:
            text_page = page.get_textpage()
            texts.append(
                normalize_text(text_page.get_text_range() or "")
            )
        return texts
    finally:
        pdf.close()


def render_page_image(file_path: str, page_index: int) -> bytes:
    """渲染 PDF 单页为 JPEG 字节，长边不超过 OCR_MAX_IMAGE_EDGE。"""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(file_path)
    try:
        page = pdf[page_index - 1]
        bitmap = page.render(scale=2)
        pil_image = bitmap.to_pil()
    finally:
        pdf.close()

    from PIL import Image

    image = pil_image.convert("RGB")
    image.thumbnail(
        (settings.OCR_MAX_IMAGE_EDGE, settings.OCR_MAX_IMAGE_EDGE)
    )
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def page_has_images(file_path: str, page_index: int) -> bool:
    """判断页面是否包含位图对象；异常或 API 不可用时保守返回 False。"""
    global _PDFIUM_IMAGE_TYPE
    try:
        import pypdfium2 as pdfium

        if _PDFIUM_IMAGE_TYPE is None:
            _PDFIUM_IMAGE_TYPE = getattr(
                pdfium, "FPDF_PAGEOBJ_IMAGE", None
            ) or getattr(pdfium.raw, "FPDF_PAGEOBJ_IMAGE", None)
        if _PDFIUM_IMAGE_TYPE is None:
            return False
        pdf = pdfium.PdfDocument(file_path)
        try:
            page = pdf[page_index - 1]
            objects = list(
                page.get_objects(
                    filter=(_PDFIUM_IMAGE_TYPE,),
                    max_depth=2,
                )
            )
            return len(objects) > 0
        finally:
            pdf.close()
    except Exception:
        return False


def parse_pdf_document(
    file_path: str,
    *,
    document_id: str,
    document_name: str | None = None,
) -> ParsedDocument:
    """按页解析 PDF；可选 A/B 层视觉识别，产出带标记的结构化 blocks。

    首选 PyPDF2（保持既有文本基线）；PyPDF2 无法解析的畸形 PDF 回退到
    pdfium 引擎提取文本，并追加解析 warning。两个引擎都失败时抛 ValueError。
    """
    name = document_name or Path(file_path).name
    try:
        page_texts = read_page_texts_pypdf2(file_path)
        used_fallback = False
    except PyPdfError as error:
        try:
            page_texts = read_page_texts_pdfium(file_path)
        except Exception as pdfium_error:
            raise ValueError(
                f"PDF 无法解析（PyPDF2: {type(error).__name__}；"
                f"备用引擎: {type(pdfium_error).__name__}）"
            ) from pdfium_error
        used_fallback = True

    # 仅 OCR 层开启时才需要文件哈希（缓存键）；关闭时保持零文件读取开销。
    file_hash = (
        file_sha256(file_path)
        if (_scanned_ocr_enabled() or _page_images_enabled())
        else ""
    )
    budget = VisionBudget()
    units: list[ParsedUnit] = []
    warnings: list[ParseWarning] = []

    for index, page_text in enumerate(page_texts, start=1):
        locator = SourceLocator(
            kind="page",
            unit_index=index - 1,
            location_label=f"第 {index} 页",
            page_number=index,
        )
        if used_fallback and index == 1:
            warnings.append(
                ParseWarning(
                    code="pdf_reader_fallback",
                    message=(
                        "该 PDF 结构异常（PyPDF2 无法解析），"
                        "已使用备用引擎提取文本"
                    ),
                    locator=locator,
                )
            )
        text = page_text
        ocr_applied = False

        if not text and _scanned_ocr_enabled():
            recognized = run_vision(
                file_hash,
                f"pdf-page:{index}",
                render_page_image(file_path, index),
                recognize_text,
                budget=budget,
                warnings=warnings,
                locator=locator,
                code="ocr_page_failed",
            )
            if recognized:
                text = recognized
                ocr_applied = True

        if not text:
            text = _EMPTY_PAGE_TEMPLATE.format(page=index)

        attributes = ocr_attributes("scanned_page") if ocr_applied else {}
        drafts = [
            BlockDraft(
                block_type="paragraph",
                text=paragraph,
                attributes=attributes,
            )
            for paragraph in split_paragraphs(text)
        ]

        # B 层：原生文本页含图片/图表时追加说明块（OCR 后的页不再重复描述）。
        if (
            not ocr_applied
            and text != _EMPTY_PAGE_TEMPLATE.format(page=index)
            and _page_images_enabled()
            and page_has_images(file_path, index)
        ):
            description = run_vision(
                file_hash,
                f"pdf-page-images:{index}",
                render_page_image(file_path, index),
                describe_image,
                budget=budget,
                warnings=warnings,
                locator=locator,
                code="ocr_page_description_failed",
            )
            if description:
                drafts.append(
                    BlockDraft(
                        block_type="paragraph",
                        text=description,
                        attributes=ocr_attributes("page_images"),
                    )
                )

        units.append(
            assemble_unit_from_drafts(
                document_id=document_id,
                document_name=name,
                locator=locator,
                drafts=drafts,
            )
        )

    return ParsedDocument(units=units, warnings=warnings)


def parse_pdf_units(
    file_path: str,
    *,
    document_id: str,
    document_name: str | None = None,
) -> list[ParsedUnit]:
    """统一 adapter 兼容入口；需要 warning 时使用 parse_pdf_document。"""
    return parse_pdf_document(
        file_path,
        document_id=document_id,
        document_name=document_name,
    ).units
