"""统一文档结构模型。

阶段 1 契约：各格式 parser adapter 输出 ParsedBlock，
assembler 生成 ParsedUnit，后续 chunker 只依赖规范文本和定位信息。
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

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

    @model_validator(mode="after")
    def validate_type_fields(self):
        if self.block_type == "heading" and self.heading_level is None:
            raise ValueError("heading 必须提供 heading_level")
        if self.block_type == "list_item" and self.list_level is None:
            raise ValueError("list_item 必须提供 list_level")
        if self.block_type == "table" and self.table_rows is None:
            raise ValueError("table 必须提供 table_rows")
        if self.block_type != "heading" and self.heading_level is not None:
            raise ValueError("heading_level 仅 heading 可设置")
        if self.block_type != "list_item" and self.list_level is not None:
            raise ValueError("list_level 仅 list_item 可设置")
        return self


class LocatedBlock(ParsedBlock):
    start_char: int
    end_char: int


class SourceLocator(BaseModel):
    kind: LocatorKind
    unit_index: int
    location_label: str
    page_number: int | None = None
    slide_number: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    sheet_name: str | None = None
    cell_range: str | None = None

    @model_validator(mode="after")
    def validate_kind_fields(self):
        if self.kind == "page" and self.page_number is None:
            raise ValueError("page_number 必须提供")
        if self.kind == "slide" and self.slide_number is None:
            raise ValueError("slide_number 必须提供")
        if self.kind == "slide" and self.page_number is not None:
            raise ValueError("slide 不能设置 page_number")
        return self


class ParsedUnit(BaseModel):
    unit_id: str
    document_id: str
    document_name: str
    parser_version: str
    locator: SourceLocator
    blocks: list[LocatedBlock]
    canonical_text: str

    @model_validator(mode="after")
    def validate_offsets(self):
        for block in self.blocks:
            if self.canonical_text[block.start_char:block.end_char] != block.text:
                raise ValueError("canonical_text 与 block offset 切片不一致")
        return self


def make_unit_id(document_id: str, parser_version: str, locator: SourceLocator) -> str:
    raw = f"{document_id}|{parser_version}|{locator.model_dump_json()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def make_block_id(
    document_id: str,
    parser_version: str,
    locator: SourceLocator,
    sequence: int,
) -> str:
    raw = f"{make_unit_id(document_id, parser_version, locator)}|{sequence}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def assemble_parsed_unit(
    *,
    document_id: str,
    document_name: str,
    parser_version: str,
    locator: SourceLocator,
    blocks: list[ParsedBlock],
) -> ParsedUnit:
    ordered = sorted(blocks, key=lambda block: block.sequence)
    sequences = [block.sequence for block in ordered]
    if len(sequences) != len(set(sequences)):
        raise ValueError("sequence 存在重复")

    parts = []
    located = []
    cursor = 0
    for block in ordered:
        expected_id = make_block_id(
            document_id, parser_version, locator, block.sequence
        )
        if block.block_id != expected_id:
            raise ValueError("block_id 不符合确定性规则")
        if parts:
            cursor += 2
        start = cursor
        parts.append(block.text)
        cursor += len(block.text)
        located.append(
            LocatedBlock(
                **block.model_dump(),
                start_char=start,
                end_char=cursor,
            )
        )

    return ParsedUnit(
        unit_id=make_unit_id(document_id, parser_version, locator),
        document_id=document_id,
        document_name=document_name,
        parser_version=parser_version,
        locator=locator,
        blocks=located,
        canonical_text="\n\n".join(parts),
    )
