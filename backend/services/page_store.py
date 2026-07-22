"""SQLite 规范页快照存储——管理 document_pages 表。

同一文档同一 parser 版本的整批页面必须在单事务内替换，禁止逐页提交。
"""

import sqlite3
from contextlib import contextmanager
from typing import Optional

from backend.config import settings
from backend.services.document_store import _connect, _connection, _now


_PAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS document_pages (
    document_id TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    page_number INTEGER NOT NULL CHECK (page_number >= 1),
    parsed_page_text TEXT NOT NULL,
    content_type TEXT NOT NULL CHECK (
        content_type IN ('paragraph', 'table', 'mixed')
    ),
    created_at TEXT NOT NULL,
    PRIMARY KEY (document_id, parser_version, page_number)
)
"""


def _ensure_page_schema(db_path: Optional[str] = None) -> None:
    """幂等创建 document_pages 表，同时确保外键已启用。"""
    connection = _connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(_PAGE_SCHEMA)
        connection.commit()
    finally:
        connection.close()


def replace_document_pages(
    document_id: str,
    parser_version: str,
    pages: list[dict],
    *,
    db_path: Optional[str] = None,
) -> None:
    """事务性替换同一 (document_id, parser_version) 的全部页快照。

    Args:
        document_id: 稳定文档 ID。
        parser_version: 固定解析器版本字符串。
        pages: dict 列表，至少包含 document_id、parser_version、page_number、
               parsed_page_text、content_type、created_at。
        db_path: 可选数据库路径，测试时可注入独立路径。

    Raises:
        ValueError: 传入页面不属于同一 document/parser 版本，或 page_number < 1。
        sqlite3.Error: 写入失败时自动回滚。
    """
    if not pages:
        raise ValueError("pages 不能为空")

    for page in pages:
        if page.get("document_id") != document_id:
            raise ValueError(
                f"页面 document_id 不一致: 期望 {document_id}，发现 {page.get('document_id')}"
            )
        if page.get("parser_version") != parser_version:
            raise ValueError(
                f"页面 parser_version 不一致: 期望 {parser_version}，发现 {page.get('parser_version')}"
            )
        if not isinstance(page.get("page_number"), int) or page["page_number"] < 1:
            raise ValueError(f"page_number 必须 >= 1: {page.get('page_number')}")

    _ensure_page_schema(db_path)
    with _connection(db_path) as connection:
        connection.execute(
            """
            DELETE FROM document_pages
            WHERE document_id = ? AND parser_version = ?
            """,
            (document_id, parser_version),
        )
        connection.executemany(
            """
            INSERT INTO document_pages (
                document_id, parser_version, page_number,
                parsed_page_text, content_type, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    page["document_id"],
                    page["parser_version"],
                    page["page_number"],
                    page["parsed_page_text"],
                    page["content_type"],
                    page["created_at"],
                )
                for page in pages
            ],
        )


def get_document_page(
    document_id: str,
    parser_version: str,
    page_number: int,
    *,
    db_path: Optional[str] = None,
) -> Optional[dict]:
    """按 (document_id, parser_version, page_number) 获取单页快照。

    Returns:
        dict 或 None（页不存在时）。
    """
    _ensure_page_schema(db_path)
    with _connection(db_path) as connection:
        row = connection.execute(
            """
            SELECT * FROM document_pages
            WHERE document_id = ? AND parser_version = ? AND page_number = ?
            """,
            (document_id, parser_version, page_number),
        ).fetchone()
    return dict(row) if row else None


def delete_document_pages(
    document_id: str,
    *,
    db_path: Optional[str] = None,
) -> None:
    """删除指定 document_id 的全部页快照（所有 parser 版本）。"""
    _ensure_page_schema(db_path)
    with _connection(db_path) as connection:
        connection.execute(
            "DELETE FROM document_pages WHERE document_id = ?",
            (document_id,),
        )
