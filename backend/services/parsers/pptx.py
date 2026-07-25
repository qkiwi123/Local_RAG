"""PPTX adapter: python-pptx -> ParsedDocument。"""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import PP_PLACEHOLDER

from backend.models.document_structure import (
    ParsedDocument,
    ParsedUnit,
    ParseWarning,
    SourceLocator,
)
from backend.services.parsers.assembler import BlockDraft, assemble_unit_from_drafts
from backend.services.parsers.office_zip import validate_office_package
from backend.services.parsers.tables import render_table_markdown
from backend.services.parsers.text import normalize_text


_LIST_PLACEHOLDER_TYPES = {
    PP_PLACEHOLDER.BODY,
    PP_PLACEHOLDER.OBJECT,
}


def _shape_position(shape, original_index: int) -> tuple[int, int, int]:
    return (
        int(getattr(shape, "top", 0) or 0),
        int(getattr(shape, "left", 0) or 0),
        original_index,
    )


def _has_direct_bullet(paragraph) -> bool:
    properties = paragraph._p.pPr
    if properties is None:
        return False
    child_names = {
        child.tag.rsplit("}", 1)[-1]
        for child in properties
    }
    if "buNone" in child_names:
        return False
    return bool(child_names & {"buChar", "buAutoNum", "buBlip"})


def _is_list_paragraph(shape, paragraph) -> bool:
    if paragraph.level > 0 or _has_direct_bullet(paragraph):
        return True
    if not getattr(shape, "is_placeholder", False):
        return False
    try:
        return shape.placeholder_format.type in _LIST_PLACEHOLDER_TYPES
    except (AttributeError, ValueError):
        return False


def _table_rows(table) -> list[list[str]]:
    rows = [
        [normalize_text(cell.text) for cell in row.cells]
        for row in table.rows
    ]
    return [row for row in rows if any(row)]


def parse_pptx_document(
    file_path: str,
    *,
    document_id: str,
    document_name: str | None = None,
) -> ParsedDocument:
    """解析原生文本和表格；无原生文字 slide 只产生 warning，不伪造正文。"""
    name = document_name or Path(file_path).name
    validate_office_package(file_path)
    presentation = Presentation(file_path)
    units: list[ParsedUnit] = []
    warnings: list[ParseWarning] = []

    for slide_number, slide in enumerate(presentation.slides, start=1):
        locator = SourceLocator(
            kind="slide",
            unit_index=slide_number - 1,
            location_label=f"第 {slide_number} 张幻灯片",
            slide_number=slide_number,
        )
        drafts: list[BlockDraft] = []
        title_shape = slide.shapes.title
        title_id = title_shape.shape_id if title_shape is not None else None
        title_text = normalize_text(title_shape.text) if title_shape is not None else ""

        if title_text:
            drafts.append(
                BlockDraft(
                    block_type="heading",
                    text=title_text,
                    heading_level=1,
                    attributes={"shape_name": title_shape.name},
                )
            )

        positioned_shapes = sorted(
            [
                (shape, index)
                for index, shape in enumerate(slide.shapes)
                if shape.shape_id != title_id
            ],
            key=lambda item: _shape_position(item[0], item[1]),
        )
        for shape, _original_index in positioned_shapes:
            if getattr(shape, "has_table", False):
                rows = _table_rows(shape.table)
                if rows:
                    drafts.append(
                        BlockDraft(
                            block_type="table",
                            text=render_table_markdown(rows),
                            table_rows=rows,
                            attributes={"shape_name": shape.name},
                        )
                    )
                continue

            if not getattr(shape, "has_text_frame", False):
                continue
            for paragraph in shape.text_frame.paragraphs:
                text = normalize_text(paragraph.text)
                if not text:
                    continue
                if _is_list_paragraph(shape, paragraph):
                    drafts.append(
                        BlockDraft(
                            block_type="list_item",
                            text=text,
                            list_level=paragraph.level,
                            attributes={"shape_name": shape.name},
                        )
                    )
                else:
                    drafts.append(
                        BlockDraft(
                            block_type="paragraph",
                            text=text,
                            attributes={"shape_name": shape.name},
                        )
                    )

        if not drafts:
            warnings.append(
                ParseWarning(
                    code="pptx_no_native_text",
                    message=(
                        f"第 {slide_number} 张幻灯片未提取到原生文本或表格"
                    ),
                    locator=locator,
                )
            )
            continue

        heading_path = [title_text] if title_text else []
        unit_locator = locator.model_copy(update={"heading_path": heading_path})
        units.append(
            assemble_unit_from_drafts(
                document_id=document_id,
                document_name=name,
                locator=unit_locator,
                drafts=drafts,
            )
        )

    return ParsedDocument(units=units, warnings=warnings)


def parse_pptx_units(
    file_path: str,
    *,
    document_id: str,
    document_name: str | None = None,
) -> list[ParsedUnit]:
    """统一 adapter 兼容入口；需要 warning 时使用 parse_pptx_document。"""
    return parse_pptx_document(
        file_path,
        document_id=document_id,
        document_name=document_name,
    ).units
