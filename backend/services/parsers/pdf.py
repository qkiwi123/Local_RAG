"""PDF adapter: PyPDF2 -> ParsedUnit。"""

from __future__ import annotations

from pathlib import Path

from PyPDF2 import PdfReader

from backend.models.document_structure import ParsedUnit, SourceLocator
from backend.services.parsers.assembler import BlockDraft, assemble_unit_from_drafts
from backend.services.parsers.text import normalize_text, split_paragraphs


_EMPTY_PAGE_TEMPLATE = "[第{page}页无可提取文本]"


def parse_pdf_units(
    file_path: str,
    *,
    document_id: str,
    document_name: str | None = None,
) -> list[ParsedUnit]:
    """Parse PDF pages into structured units."""
    name = document_name or Path(file_path).name
    reader = PdfReader(file_path)
    units: list[ParsedUnit] = []

    for index, page in enumerate(reader.pages, start=1):
        text = normalize_text(page.extract_text() or "")
        if not text:
            text = _EMPTY_PAGE_TEMPLATE.format(page=index)

        locator = SourceLocator(
            kind="page",
            unit_index=index - 1,
            location_label=f"第 {index} 页",
            page_number=index,
        )

        drafts = [
            BlockDraft(
                block_type="paragraph",
                text=paragraph,
            )
            for paragraph in split_paragraphs(text)
        ]

        units.append(
            assemble_unit_from_drafts(
                document_id=document_id,
                document_name=name,
                locator=locator,
                drafts=drafts,
            )
        )

    return units
