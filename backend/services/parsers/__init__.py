"""统一文档格式适配器入口。"""

from pathlib import Path

from backend.models.document_structure import ParsedDocument, ParsedUnit
from backend.services.parsers.constants import PARSER_VERSION
from backend.services.parsers.docx import parse_docx_document, parse_docx_units
from backend.services.parsers.markdown import parse_markdown_units
from backend.services.parsers.pdf import parse_pdf_document, parse_pdf_units
from backend.services.parsers.pptx import parse_pptx_document, parse_pptx_units
from backend.services.parsers.txt import parse_txt_units


ADAPTERS = {
    ".pdf": parse_pdf_units,
    ".docx": parse_docx_units,
    ".txt": parse_txt_units,
    ".md": parse_markdown_units,
    ".pptx": parse_pptx_units,
}


def _units_only_document(adapter):
    """把只返回 units 的 adapter 包装为 ParsedDocument（无 warning）。"""

    def wrapper(file_path: str, *, document_id: str, document_name=None):
        return ParsedDocument(
            units=adapter(
                file_path,
                document_id=document_id,
                document_name=document_name,
            )
        )

    return wrapper


DOCUMENT_ADAPTERS = {
    ".pdf": parse_pdf_document,
    ".docx": parse_docx_document,
    ".txt": _units_only_document(parse_txt_units),
    ".md": _units_only_document(parse_markdown_units),
    ".pptx": parse_pptx_document,
}


def parse_document_units(
    file_path: str,
    *,
    document_id: str,
    document_name: str | None = None,
) -> list[ParsedUnit]:
    """按后缀分发到结构化 adapter，不产生旧 page-dict 中间格式。"""
    suffix = Path(file_path).suffix.lower()
    adapter = ADAPTERS.get(suffix)
    if adapter is None:
        supported = ", ".join(sorted(ADAPTERS))
        raise ValueError(f"不支持的文件格式: {suffix}（当前支持: {supported}）")
    return adapter(
        file_path,
        document_id=document_id,
        document_name=document_name,
    )


def parse_document_structure(
    file_path: str,
    *,
    document_id: str,
    document_name: str | None = None,
) -> ParsedDocument:
    """返回结构化 units 与非致命解析/OCR warnings。"""
    suffix = Path(file_path).suffix.lower()
    adapter = DOCUMENT_ADAPTERS.get(suffix)
    if adapter is None:
        supported = ", ".join(sorted(DOCUMENT_ADAPTERS))
        raise ValueError(f"不支持的文件格式: {suffix}（当前支持: {supported}）")
    return adapter(
        file_path,
        document_id=document_id,
        document_name=document_name,
    )


__all__ = [
    "PARSER_VERSION",
    "ADAPTERS",
    "DOCUMENT_ADAPTERS",
    "parse_document_units",
    "parse_document_structure",
    "parse_pdf_units",
    "parse_pdf_document",
    "parse_docx_units",
    "parse_docx_document",
    "parse_markdown_units",
    "parse_pptx_document",
    "parse_pptx_units",
    "parse_txt_units",
]
