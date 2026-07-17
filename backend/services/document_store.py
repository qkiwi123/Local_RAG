"""SQLite 文档登记表。

原始文件名只用于展示；document_id 才是文件、向量和删除操作之间的稳定主键。
每个函数使用短连接，避免 FastAPI 多线程环境共享 sqlite connection。
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from backend.config import settings


_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    original_name TEXT NOT NULL,
    stored_name TEXT NOT NULL UNIQUE,
    size_bytes INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('processing', 'ready', 'failed')),
    chunk_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = Path(db_path or settings.METADATA_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute(_SCHEMA)
    connection.commit()
    return connection


@contextmanager
def _connection(db_path: Optional[str] = None):
    """事务化短连接；sqlite3.Connection 自身的 with 不会负责 close。"""
    connection = _connect(db_path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def create_document(
    document_id: str,
    original_name: str,
    stored_name: str,
    size_bytes: int,
    db_path: Optional[str] = None,
) -> dict:
    """登记一条 processing 状态的入库记录。"""
    timestamp = _now()
    with _connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO documents (
                document_id, original_name, stored_name, size_bytes,
                status, chunk_count, error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'processing', 0, NULL, ?, ?)
            """,
            (
                document_id,
                original_name,
                stored_name,
                size_bytes,
                timestamp,
                timestamp,
            ),
        )
    return get_document(document_id, db_path)


def mark_ready(
    document_id: str,
    chunk_count: int,
    db_path: Optional[str] = None,
) -> None:
    with _connection(db_path) as connection:
        cursor = connection.execute(
            """
            UPDATE documents
            SET status = 'ready', chunk_count = ?, error = NULL, updated_at = ?
            WHERE document_id = ?
            """,
            (chunk_count, _now(), document_id),
        )
        if cursor.rowcount != 1:
            raise KeyError(f"文档记录不存在: {document_id}")


def mark_failed(
    document_id: str,
    error: str,
    db_path: Optional[str] = None,
) -> None:
    with _connection(db_path) as connection:
        cursor = connection.execute(
            """
            UPDATE documents
            SET status = 'failed', chunk_count = 0, error = ?, updated_at = ?
            WHERE document_id = ?
            """,
            (error[:2000], _now(), document_id),
        )
        if cursor.rowcount != 1:
            raise KeyError(f"文档记录不存在: {document_id}")


def get_document(document_id: str, db_path: Optional[str] = None) -> Optional[dict]:
    with _connection(db_path) as connection:
        row = connection.execute(
            "SELECT * FROM documents WHERE document_id = ?",
            (document_id,),
        ).fetchone()
    return dict(row) if row else None


def list_documents(
    status: Optional[str] = "ready",
    db_path: Optional[str] = None,
) -> list[dict]:
    with _connection(db_path) as connection:
        if status is None:
            rows = connection.execute(
                "SELECT * FROM documents ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM documents WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
    return [dict(row) for row in rows]


def delete_record(document_id: str, db_path: Optional[str] = None) -> bool:
    with _connection(db_path) as connection:
        cursor = connection.execute(
            "DELETE FROM documents WHERE document_id = ?",
            (document_id,),
        )
    return cursor.rowcount == 1
