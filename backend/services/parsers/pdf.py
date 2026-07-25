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


def parse_pdf_units(
    file_path: str,
    *,
    document_id: str,
    document_name: str | None = None,
):
    """Parse PDF into structured units.

    Each PDF page becomes one logical unit. Empty pages are skipped because they
    do not provide retrieval evidence.
    """
    name = document_name or Path(file_path).name
    reader = PdfReader(file_path)
    units = []

    for index, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if not text:
            continue

        locator = SourceLocator(
            kind="page",
            unit_index=index - 1,
            location_label=f"第 {index} 页",
            page_number=index,
        )
        block = ParsedBlock(
            block_id=make_block_id(
                document_id,
                PARSER_VERSION,
                locator,
                0,
            ),
            block_type="paragraph",
            text=text,
            sequence=0,
        )
        units.append(
            assemble_parsed_unit(
                document_id=document_id,
                document_name=name,
                parser_version=PARSER_VERSION,
                locator=locator,
                blocks=[block],
            )
        )

    return units
