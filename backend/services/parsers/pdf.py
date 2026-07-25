"""PDF adapter: PyPDF2 -> ParsedUnit."""

from pathlib import Path

from PyPDF2 import PdfReader

from backend.models.document_structure import (
    ParsedBlock,
    SourceLocator,
    assemble_parsed_unit,
    make_block_id,
)
from backend.services.parsers import PARSER_VERSION
from backend.services.parsers.text import normalize_text, split_paragraphs


_EMPTY_PAGE_TEMPLATE = "[第{page}页无可提取文本]"


def parse_pdf_units(
    file_path: str,
    *,
    document_id: str,
    document_name: str | None = None,
):
    """Parse PDF pages into structured units."""
    name = document_name or Path(file_path).name
    reader = PdfReader(file_path)
    units = []

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

        blocks = []
        for sequence, paragraph in enumerate(split_paragraphs(text)):
            blocks.append(
                ParsedBlock(
                    block_id=make_block_id(
                        document_id,
                        PARSER_VERSION,
                        locator,
                        sequence,
                    ),
                    block_type="paragraph",
                    text=paragraph,
                    sequence=sequence,
                )
            )

        units.append(
            assemble_parsed_unit(
                document_id=document_id,
                document_name=name,
                parser_version=PARSER_VERSION,
                locator=locator,
                blocks=blocks,
            )
        )

    return units
