"""TXT adapter: plain text -> ParsedUnit。"""

from __future__ import annotations

from pathlib import Path

from backend.models.document_structure import (
    ParsedBlock,
    ParsedUnit,
    SourceLocator,
    assemble_parsed_unit,
    make_block_id,
)
from backend.services.parsers import PARSER_VERSION
from backend.services.parsers.text import normalize_text, split_paragraphs


LINES_PER_VIRTUAL_PAGE = 50


def parse_txt_units(
    file_path: str,
    *,
    document_id: str,
    document_name: str | None = None,
) -> list[ParsedUnit]:
    """TXT 无真实分页，按固定行数生成 virtual_page 单元。"""
    name = document_name or Path(file_path).name
    with open(file_path, "r", encoding="utf-8") as file:
        lines = file.readlines()

    units: list[ParsedUnit] = []
    for line_offset in range(0, len(lines), LINES_PER_VIRTUAL_PAGE):
        page_number = line_offset // LINES_PER_VIRTUAL_PAGE + 1
        text = normalize_text(
            "".join(lines[line_offset:line_offset + LINES_PER_VIRTUAL_PAGE])
        )
        if not text:
            text = f"[逻辑页{page_number}无可提取文本]"

        locator = SourceLocator(
            kind="virtual_page",
            unit_index=page_number - 1,
            location_label=f"逻辑页 {page_number}",
            page_number=page_number,
        )

        paragraphs = split_paragraphs(text) or [text]
        blocks = [
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
            for sequence, paragraph in enumerate(paragraphs)
        ]

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
