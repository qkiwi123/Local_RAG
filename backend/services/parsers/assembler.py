"""Parser adapter 共用的 unit 组装器。

Adapter 只负责识别来源位置和文档结构，稳定 ID、连续 sequence、字符 offset
及 canonical_text 均在这里统一生成。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.models.document_structure import (
    BlockType,
    ParsedBlock,
    ParsedUnit,
    SourceLocator,
    assemble_parsed_unit,
    make_block_id,
)
from backend.services.parsers.constants import PARSER_VERSION


@dataclass(frozen=True)
class BlockDraft:
    """尚未绑定稳定 ID 和 sequence 的结构块草稿。"""

    block_type: BlockType
    text: str
    heading_level: int | None = None
    list_level: int | None = None
    table_rows: list[list[str]] | None = None
    attributes: dict[str, str | int | bool] = field(default_factory=dict)


def assemble_unit_from_drafts(
    *,
    document_id: str,
    document_name: str,
    locator: SourceLocator,
    drafts: list[BlockDraft],
    parser_version: str = PARSER_VERSION,
) -> ParsedUnit:
    """将 adapter 草稿统一转换为经过完整校验的 ParsedUnit。"""
    if not drafts:
        raise ValueError("unit drafts 不能为空")

    blocks = [
        ParsedBlock(
            block_id=make_block_id(
                document_id,
                parser_version,
                locator,
                sequence,
            ),
            block_type=draft.block_type,
            text=draft.text,
            sequence=sequence,
            heading_level=draft.heading_level,
            list_level=draft.list_level,
            table_rows=draft.table_rows,
            attributes=draft.attributes,
        )
        for sequence, draft in enumerate(drafts)
    ]
    return assemble_parsed_unit(
        document_id=document_id,
        document_name=document_name,
        parser_version=parser_version,
        locator=locator,
        blocks=blocks,
    )
