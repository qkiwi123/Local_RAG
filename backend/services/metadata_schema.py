"""新结构化 metadata SQLite 的显式 schema bootstrap。

旧数据库不迁移、不猜测；只有空数据库会被初始化。发现无版本或不受支持的
schema 时拒绝继续写入，避免在旧生产 metadata.db 上静默混用新旧表。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional


SCHEMA_VERSION = 1

SCHEMA_META = """
CREATE TABLE schema_meta (
    schema_version INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL
)
"""

DOCUMENTS_SCHEMA = """
CREATE TABLE documents (
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

DOCUMENT_UNITS_SCHEMA = """
CREATE TABLE document_units (
    document_id TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    unit_index INTEGER NOT NULL CHECK (unit_index >= 0),
    locator_kind TEXT NOT NULL,
    location_label TEXT NOT NULL,
    canonical_text TEXT NOT NULL,
    structure_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (document_id, parser_version, unit_id),
    FOREIGN KEY (document_id)
        REFERENCES documents(document_id) ON DELETE CASCADE
)
"""

CONVERSATIONS_SCHEMA = """
CREATE TABLE conversations (
    conversation_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

MESSAGES_SCHEMA = """
CREATE TABLE messages (
    message_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'complete', 'failed')),
    standalone_question TEXT,
    citations_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (conversation_id, sequence),
    UNIQUE (request_id, role),
    FOREIGN KEY (conversation_id)
        REFERENCES conversations(conversation_id) ON DELETE CASCADE
)
"""

CHAT_REQUESTS_SCHEMA = """
CREATE TABLE chat_requests (
    request_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    expected_last_message_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('pending', 'complete', 'failed')),
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (conversation_id)
        REFERENCES conversations(conversation_id) ON DELETE CASCADE
)
"""

_SCHEMAS = (
    SCHEMA_META,
    DOCUMENTS_SCHEMA,
    DOCUMENT_UNITS_SCHEMA,
    CONVERSATIONS_SCHEMA,
    MESSAGES_SCHEMA,
    CHAT_REQUESTS_SCHEMA,
)
_REQUIRED_TABLES = {
    "schema_meta",
    "documents",
    "document_units",
    "conversations",
    "messages",
    "chat_requests",
}


class UnsupportedSchemaError(RuntimeError):
    pass


def _user_tables(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        """
    ).fetchall()
    return {row[0] for row in rows}


def _validate_existing_schema(
    connection: sqlite3.Connection,
    tables: set[str],
) -> None:
    if "schema_meta" not in tables:
        raise UnsupportedSchemaError(
            "检测到未版本化 metadata 数据库；拒绝自动迁移或混写"
        )
    rows = connection.execute(
        "SELECT schema_version FROM schema_meta ORDER BY schema_version"
    ).fetchall()
    versions = [row[0] for row in rows]
    if versions != [SCHEMA_VERSION]:
        raise UnsupportedSchemaError(
            f"不支持的 metadata schema version: {versions}"
        )
    missing = _REQUIRED_TABLES - tables
    if missing:
        raise UnsupportedSchemaError(
            "metadata schema 不完整，缺少表: " + ", ".join(sorted(missing))
        )


def bootstrap_structured_database(
    db_path: str,
    *,
    created_at: str,
) -> None:
    """仅初始化空数据库；已有库必须已是完整且受支持的 schema。"""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        tables = _user_tables(connection)
        if tables:
            _validate_existing_schema(connection, tables)
            return

        connection.execute("BEGIN IMMEDIATE")
        for schema in _SCHEMAS:
            connection.execute(schema)
        connection.execute(
            "INSERT INTO schema_meta (schema_version, created_at) VALUES (?, ?)",
            (SCHEMA_VERSION, created_at),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def require_structured_database(db_path: Optional[str]) -> None:
    if not db_path:
        raise ValueError("结构化数据库校验必须提供明确 db_path")
    connection = sqlite3.connect(db_path, timeout=10)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        _validate_existing_schema(connection, _user_tables(connection))
    finally:
        connection.close()
