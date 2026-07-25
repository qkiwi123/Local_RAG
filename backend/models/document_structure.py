"""统一文档解析结构模型。

阶段 1 的基础契约：parser adapter 输出 ParsedBlock，unit assembler
生成 LocatedBlock 和 ParsedUnit。
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, Field, field_validator


BlockType = Literal["heading", "paragraph", "list_item", "table", "code", "quote"]
LocatorKind = Literal["page", "virtual_page", "slide", "section", "sheet"]


class ParsedBlock(BaseModel):
    block_id: str
    block_type: BlockType
    text: str
    sequence: int
    heading_level: int | None = None
    list_level: int | None = None
    table_rows: list[list[str]] | None = None
    attributes: dict[str, str | int | bool] = Field(default_factory=dict)

    @field_validator("heading_level")
    @classmethod
    def validate_heading_level(cls, value):
        if value is not None and not 1 <= value <= 6:
            raise ValueError("heading_level must be between 1 and 6")
        return value

    @field_validator("list_level")
    @classmethod
    def validate_list_level(cls, value):
        if value is not None and value < 0:
            raise ValueError("list_level must be >= 0")
        return value


class LocatedBlock(ParsedBlock):
    start_char: int
    end_char: int

    def validate_range(self, canonical_text: str):
        if canonical_text[self.start_char:self.end_char] != self.text:
            raise ValueError("located block range does not match canonical text")


class SourceLocator(BaseModel):
    kind: LocatorKind
    unit_index: int
    location_label: str
    page_number: int | None = None
    slide_number: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    sheet_name: str | None = None
    cell_range: str | None = None


class ParsedUnit(BaseModel):
    unit_id: str
    document_id: str
    document_name: str
    parser_version: str
    locator: SourceLocator
    blocks: list[LocatedBlock]
    canonical_text: str


def build_canonical_text(blocks: list[ParsedBlock]) -> tuple[str, list[LocatedBlock]]:
    parts: list[str] = []
    located: list[LocatedBlock] = []
    cursor = 0
    for block in blocks:
        if parts:
            cursor += 2
        start = cursor
        parts.append(block.text)
        cursor += len(block.text)
        located.append(LocatedBlock(**block.model_dump(), start_char=start, end_char=cursor))
    return "\n\n".join(parts), located


def make_unit_id(document_id: str, parser_version: str, locator: SourceLocator) -> str:
    raw = f"{document_id}|{parser_version}|{locator.kind}|{locator.unit_index}|{locator.location_label}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
