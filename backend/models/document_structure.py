"""统一文档结构模型。

阶段 1 契约：各格式 parser adapter 生成结构化 block，assembler 生成
ParsedUnit；后续 chunker 只依赖规范文本和统一来源定位。
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

    @field_validator("block_id", "text")
    @classmethod
    def validate_non_empty_text_fields(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("字段不能为空")
        return value

    @field_validator("sequence")
    @classmethod
    def validate_sequence(cls, value: int) -> int:
        if value < 0:
            raise ValueError("sequence 必须大于等于 0")
        return value

    @field_validator("heading_level")
    @classmethod
    def validate_heading_level(cls, value: int | None) -> int | None:
        if value is not None and not 1 <= value <= 6:
            raise ValueError("heading_level must be between 1 and 6")
        return value

    @field_validator("list_level")
    @classmethod
    def validate_list_level(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("list_level must be >= 0")
        return value

    @model_validator(mode="after")
    def validate_type_fields(self):
        if self.block_type == "heading" and self.heading_level is None:
            raise ValueError("heading 必须提供 heading_level")
        if self.block_type == "list_item" and self.list_level is None:
            raise ValueError("list_item 必须提供 list_level")
        if self.block_type == "table" and not self.table_rows:
            raise ValueError("table 必须提供非空 table_rows")
        if self.block_type != "heading" and self.heading_level is not None:
            raise ValueError("heading_level 仅 heading 可设置")
        if self.block_type != "list_item" and self.list_level is not None:
            raise ValueError("list_level 仅 list_item 可设置")
        if self.block_type != "table" and self.table_rows is not None:
            raise ValueError("table_rows 仅 table 可设置")
        return self


class LocatedBlock(ParsedBlock):
    start_char: int
    end_char: int

    @model_validator(mode="after")
    def validate_range(self):
        if self.start_char < 0 or self.end_char <= self.start_char:
            raise ValueError("block 字符范围非法")
        if self.end_char - self.start_char != len(self.text):
            raise ValueError("block 字符范围长度与 text 不一致")
        return self


class SourceLocator(BaseModel):
    kind: LocatorKind
    unit_index: int
    location_label: str
    page_number: int | None = None
    slide_number: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    sheet_name: str | None = None
    cell_range: str | None = None

    @field_validator("unit_index")
    @classmethod
    def validate_unit_index(cls, value: int) -> int:
        if value < 0:
            raise ValueError("unit_index 必须大于等于 0")
        return value

    @field_validator("location_label")
    @classmethod
    def validate_location_label(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("location_label 不能为空")
        return value

    @field_validator("heading_path")
    @classmethod
    def validate_heading_path(cls, value: list[str]) -> list[str]:
        if any(not item or not item.strip() for item in value):
            raise ValueError("heading_path 不能包含空标题")
        return value

    @model_validator(mode="after")
    def validate_kind_fields(self):
        if self.kind in {"page", "virtual_page"}:
            if self.page_number is None or self.page_number < 1:
                raise ValueError("page_number 必须提供且大于等于 1")
        elif self.page_number is not None:
            raise ValueError("只有 page 和 virtual_page 可以设置 page_number")

        if self.kind == "slide":
            if self.slide_number is None or self.slide_number < 1:
                raise ValueError("slide_number 必须提供且大于等于 1")
        elif self.slide_number is not None:
            raise ValueError("只有 slide 可以设置 slide_number")

        if self.kind == "sheet":
            if not self.sheet_name:
                raise ValueError("sheet 必须提供 sheet_name")
        elif self.sheet_name is not None or self.cell_range is not None:
            raise ValueError("sheet_name/cell_range 仅 sheet 可以设置")
        return self


class ParsedUnit(BaseModel):
    unit_id: str
    document_id: str
    document_name: str
    parser_version: str
    locator: SourceLocator
    blocks: list[LocatedBlock]
    canonical_text: str

    @field_validator("unit_id", "document_id", "document_name", "parser_version")
    @classmethod
    def validate_identity_fields(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("标识字段不能为空")
        return value

    @model_validator(mode="after")
    def validate_structure(self):
        if not self.blocks:
            raise ValueError("ParsedUnit 必须至少包含一个 block")

        expected_unit_id = make_unit_id(
            self.document_id,
            self.parser_version,
            self.locator,
        )
        if self.unit_id != expected_unit_id:
            raise ValueError("unit_id 不符合确定性规则")

        sequences = [block.sequence for block in self.blocks]
        if sequences != list(range(len(self.blocks))):
            raise ValueError("block sequence 必须从 0 连续递增")
        if len({block.block_id for block in self.blocks}) != len(self.blocks):
            raise ValueError("block_id 不能重复")

        expected_text = "\n\n".join(block.text for block in self.blocks)
        if self.canonical_text != expected_text:
            raise ValueError("canonical_text 必须由 block.text 使用双换行连接")

        cursor = 0
        for index, block in enumerate(self.blocks):
            expected_block_id = make_block_id(
                self.document_id,
                self.parser_version,
                self.locator,
                block.sequence,
            )
            if block.block_id != expected_block_id:
                raise ValueError("block_id 不符合确定性规则")

            if index:
                cursor += 2
            expected_start = cursor
            expected_end = expected_start + len(block.text)
            if block.start_char != expected_start or block.end_char != expected_end:
                raise ValueError("canonical_text 与 block offset 切片不一致")
            if self.canonical_text[block.start_char:block.end_char] != block.text:
                raise ValueError("canonical_text 与 block offset 切片不一致")
            cursor = expected_end
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
    if not blocks:
        raise ValueError("ParsedUnit 必须至少包含一个 block")

    ordered = sorted(blocks, key=lambda block: block.sequence)
    sequences = [block.sequence for block in ordered]
    if len(sequences) != len(set(sequences)):
        raise ValueError("sequence 存在重复")
    if sequences != list(range(len(ordered))):
        raise ValueError("sequence 必须从 0 连续递增")

    parts: list[str] = []
    located: list[LocatedBlock] = []
    cursor = 0
    for block in ordered:
        expected_id = make_block_id(
            document_id,
            parser_version,
            locator,
            block.sequence,
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
