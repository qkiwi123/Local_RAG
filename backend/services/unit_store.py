"""结构化来源单元的 SQLite 存储。

只保存 ParsedUnit 的规范文本、locator 和 LocatedBlock；读取时重新组装并
通过 Pydantic 校验，数据库 JSON 不作为可信对象直接向外传递。
"""

from __future__ import annotations

import json
from typing import Optional

from backend.models.document_structure import ParsedUnit
from backend.services.document_store import _connection, _now
from backend.services.metadata_schema import require_structured_database


def _ensure_unit_schema(db_path: Optional[str] = None) -> None:
    require_structured_database(db_path)


def _validated_units(units: list[ParsedUnit | dict]) -> list[ParsedUnit]:
    return [
        unit if isinstance(unit, ParsedUnit) else ParsedUnit.model_validate(unit)
        for unit in units
    ]


def _structure_json(unit: ParsedUnit) -> str:
    return json.dumps(
        {
            "locator": unit.locator.model_dump(mode="json"),
            "blocks": [
                block.model_dump(mode="json")
                for block in unit.blocks
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def replace_document_units(
    document_id: str,
    parser_version: str,
    units: list[ParsedUnit | dict],
    *,
    db_path: Optional[str] = None,
) -> None:
    """在单个事务中替换文档同一 parser 版本的全部来源单元。"""
    if not units:
        raise ValueError("units 不能为空")

    validated = _validated_units(units)
    for unit in validated:
        if unit.document_id != document_id:
            raise ValueError(
                f"unit document_id 不一致: 期望 {document_id}，"
                f"发现 {unit.document_id}"
            )
        if unit.parser_version != parser_version:
            raise ValueError(
                f"unit parser_version 不一致: 期望 {parser_version}，"
                f"发现 {unit.parser_version}"
            )

    unit_ids = [unit.unit_id for unit in validated]
    unit_indexes = [unit.locator.unit_index for unit in validated]
    if len(unit_ids) != len(set(unit_ids)):
        raise ValueError("unit_id 不能重复")
    if len(unit_indexes) != len(set(unit_indexes)):
        raise ValueError("unit_index 不能重复")

    _ensure_unit_schema(db_path)
    created_at = _now()
    with _connection(db_path) as connection:
        connection.execute(
            """
            DELETE FROM document_units
            WHERE document_id = ? AND parser_version = ?
            """,
            (document_id, parser_version),
        )
        connection.executemany(
            """
            INSERT INTO document_units (
                document_id, parser_version, unit_id, unit_index,
                locator_kind, location_label, canonical_text,
                structure_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    unit.document_id,
                    unit.parser_version,
                    unit.unit_id,
                    unit.locator.unit_index,
                    unit.locator.kind,
                    unit.locator.location_label,
                    unit.canonical_text,
                    _structure_json(unit),
                    created_at,
                )
                for unit in validated
            ],
        )


def _row_to_unit(row: dict) -> ParsedUnit:
    structure = json.loads(row["structure_json"])
    return ParsedUnit.model_validate(
        {
            "unit_id": row["unit_id"],
            "document_id": row["document_id"],
            "document_name": row["document_name"],
            "parser_version": row["parser_version"],
            "locator": structure["locator"],
            "blocks": structure["blocks"],
            "canonical_text": row["canonical_text"],
        }
    )


def get_document_unit(
    document_id: str,
    parser_version: str,
    unit_id: str,
    *,
    db_path: Optional[str] = None,
) -> ParsedUnit | None:
    _ensure_unit_schema(db_path)
    with _connection(db_path) as connection:
        row = connection.execute(
            """
            SELECT
                u.document_id, u.parser_version, u.unit_id, u.unit_index,
                u.locator_kind, u.location_label, u.canonical_text,
                u.structure_json, u.created_at,
                d.original_name AS document_name
            FROM document_units AS u
            JOIN documents AS d ON d.document_id = u.document_id
            WHERE u.document_id = ?
              AND u.parser_version = ?
              AND u.unit_id = ?
            """,
            (document_id, parser_version, unit_id),
        ).fetchone()
    return _row_to_unit(dict(row)) if row else None


def list_document_units(
    document_id: str,
    parser_version: str,
    *,
    db_path: Optional[str] = None,
) -> list[ParsedUnit]:
    _ensure_unit_schema(db_path)
    with _connection(db_path) as connection:
        rows = connection.execute(
            """
            SELECT
                u.document_id, u.parser_version, u.unit_id, u.unit_index,
                u.locator_kind, u.location_label, u.canonical_text,
                u.structure_json, u.created_at,
                d.original_name AS document_name
            FROM document_units AS u
            JOIN documents AS d ON d.document_id = u.document_id
            WHERE u.document_id = ? AND u.parser_version = ?
            ORDER BY u.unit_index
            """,
            (document_id, parser_version),
        ).fetchall()
    return [_row_to_unit(dict(row)) for row in rows]


def delete_document_units(
    document_id: str,
    *,
    db_path: Optional[str] = None,
) -> None:
    _ensure_unit_schema(db_path)
    with _connection(db_path) as connection:
        connection.execute(
            "DELETE FROM document_units WHERE document_id = ?",
            (document_id,),
        )
