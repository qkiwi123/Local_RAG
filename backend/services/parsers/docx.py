"""DOCX adapter: python-docx -> ParsedUnit."""

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


def _table_rows(table: Table) -> list[list[str]]:
    rows = []
    for row in table.rows:
        values = [normalize_text(cell.text) for cell in row.cells]
        if any(values):
            rows.append(values)
    return rows


def parse_docx_units(
    file_path: str,
    *,
    document_id: str,
    document_name: str | None = None,
):
    """Parse DOCX preserving paragraph/table order."""
    name = document_name or Path(file_path).name
    document = Document(file_path)
    units = []
    virtual_page = 1
    sequence = 0
    blocks = []

    def flush():
        nonlocal blocks, sequence, virtual_page
        if not blocks:
            return
        locator = SourceLocator(
            kind="virtual_page",
            unit_index=virtual_page - 1,
            location_label=f"虚拟页 {virtual_page}",
            heading_path=[],
        )
        # rebuild ids with final locator
        rebuilt = []
        for block in blocks:
            rebuilt.append(
                ParsedBlock(
                    block_id=make_block_id(
                        document_id,
                        PARSER_VERSION,
                        locator,
                        block.sequence,
                    ),
                    block_type=block.block_type,
                    text=block.text,
                    sequence=block.sequence,
                    heading_level=block.heading_level,
                    list_level=block.list_level,
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
                block_type = "heading"
                level = int(style.split()[-1]) if style.split()[-1].isdigit() else 1
                block = ParsedBlock(
                    block_id="pending",
                    block_type=block_type,
                    text=text,
                    sequence=sequence,
                    heading_level=level,
                )
            else:
                block = ParsedBlock(
                    block_id="pending",
                    block_type="paragraph",
                    text=text,
                    sequence=sequence,
                )
            blocks.append(block)
            sequence += 1
            if sequence >= PARAGRAPHS_PER_VIRTUAL_PAGE:
                flush()
        elif isinstance(item, Table):
            rows = _table_rows(item)
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
