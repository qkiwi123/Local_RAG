"""TXT adapter: plain text -> ParsedUnit。"""

from __future__ import annotations

from pathlib import Path

from backend.models.document_structure import ParsedUnit, SourceLocator
from backend.services.parsers.assembler import BlockDraft, assemble_unit_from_drafts
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
            continue

        locator = SourceLocator(
            kind="virtual_page",
            unit_index=page_number - 1,
            location_label=f"逻辑页 {page_number}",
            page_number=page_number,
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
