"""PDF adapter: PyPDF2 -> ParsedUnit."""

from pathlib import Path
import re

from PyPDF2 import PdfReader

from backend.models.document_structure import (
    ParsedBlock,
    SourceLocator,
    assemble_parsed_unit,
    make_block_id,
)
from backend.services.parsers import PARSER_VERSION


_EMPTY_PAGE_TEMPLATE = "[第{page}页无可提取文本]"


def _normalize_text(text: str) -> str:
    """Normalize PDF extracted text without changing semantic content."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.translate(str.maketrans("０１２３４５６７８９ＡＢＣＤＥＦＧ", "0123456789ABCDEFG"))
    text = text.replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_pdf_units(
    file_path: str,
    *,
    document_id: str,
    document_name: str | None = None,
):
    """Parse PDF pages into structured units.

    One physical PDF page maps to one source unit. Paragraph blocks are split by
    blank lines; pages without extracted text remain as explicit empty evidence.
    """
    name = document_name or Path(file_path).name
    reader = PdfReader(file_path)
    units = []

    for index, page in enumerate(reader.pages, start=1):
        raw_text = page.extract_text() or ""
        text = _normalize_text(raw_text)
        if not text:
            text = _EMPTY_PAGE_TEMPLATE.format(page=index)

        locator = SourceLocator(
            kind="page",
            unit_index=index - 1,
            location_label=f"第 {index} 页",
            page_number=index,
        )

        blocks = []
        for sequence, paragraph in enumerate(
            part.strip() for part in text.split("\n\n") if part.strip()
        ):
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
