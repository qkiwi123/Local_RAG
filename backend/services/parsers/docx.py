"""DOCX adapter: python-docx -> ParsedUnit。"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from backend.models.document_structure import (
    ParsedBlock,
    SourceLocator,
    assemble_parsed_unit,
    make_block_id,
)
from backend.services.parsers import PARSER_VERSION
from backend.services.parsers.text import normalize_text


PARAGRAPHS_PER_VIRTUAL_PAGE = 30


def parse_docx_units(file_path: str, *, document_id: str, document_name: str | None = None):
    name = document_name or Path(file_path).name
    document = Document(file_path)
    units = []
    blocks = []
    virtual_page = 1
    sequence = 0

    def flush():
        nonlocal blocks, sequence, virtual_page
        if not blocks:
            return
        locator = SourceLocator(
            kind="virtual_page",
            unit_index=virtual_page - 1,
            location_label=f"逻辑页 {virtual_page}",
            page_number=virtual_page,
        )
        rebuilt = []
        for index, block in enumerate(blocks):
            rebuilt.append(
                ParsedBlock(
                    block_id=make_block_id(
                        document_id,
                        PARSER_VERSION,
                        locator,
                        index,
                    ),
                    block_type=block.block_type,
                    text=block.text,
                    sequence=index,
                    heading_level=block.heading_level,
                    table_rows=block.table_rows,
                )
            )
        units.append(
            assemble_parsed_unit(
                document_id=document_id,
                document_name=name,
                parser_version=PARSER_VERSION,
                locator=locator,
                blocks=rebuilt,
            )
        )
        blocks = []
        sequence = 0
        virtual_page += 1

    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            text = normalize_text(item.text)
            if not text:
                continue
            style = item.style.name.lower() if item.style else ""
            if "heading" in style:
                level = int(style.split()[-1]) if style.split()[-1].isdigit() else 1
                blocks.append(
                    ParsedBlock(
                        block_id="pending",
                        block_type="heading",
                        text=text,
                        sequence=sequence,
                        heading_level=level,
                    )
                )
            else:
                blocks.append(
                    ParsedBlock(
                        block_id="pending",
                        block_type="paragraph",
                        text=text,
                        sequence=sequence,
                    )
                )
            sequence += 1
            if sequence >= PARAGRAPHS_PER_VIRTUAL_PAGE:
                flush()
        elif isinstance(item, Table):
            rows = [[normalize_text(cell.text) for cell in row.cells] for row in item.rows]
            rows = [row for row in rows if any(row)]
            if rows:
                blocks.append(
                    ParsedBlock(
                        block_id="pending",
                        block_type="table",
                        text="\n".join("| " + " | ".join(row) + " |" for row in rows),
                        sequence=sequence,
                        table_rows=rows,
                    )
                )
                sequence += 1

    flush()
    return units
