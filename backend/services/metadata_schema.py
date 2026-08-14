"""新结构化 metadata SQLite 的显式 schema bootstrap 与版本化迁移。

- 未版本化的旧数据库不迁移、不猜测，发现后拒绝读写；
- 已版本化的数据库按 SCHEMA_VERSION 逐版本显式迁移，业务数据保留；
- 迁移只做保留性变更（新增表/列），不做派生数据转换（派生数据一律重建）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional


SCHEMA_VERSION = 3

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

# v2：多级树形标签。tag_path 为任意深度的 '/' 分隔路径，层级由前缀推导。
TAGS_SCHEMA = """
CREATE TABLE tags (
    tag_path TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""

DOCUMENT_TAGS_SCHEMA = """
CREATE TABLE document_tags (
    document_id TEXT NOT NULL,
    tag_path TEXT NOT NULL,
    PRIMARY KEY (document_id, tag_path),
    FOREIGN KEY (document_id)
        REFERENCES documents(document_id) ON DELETE CASCADE,
    FOREIGN KEY (tag_path)
        REFERENCES tags(tag_path) ON DELETE CASCADE
)
"""

# v3：检索 Trace 持久化。绑定助手消息，随会话删除级联清理；
# 总量由 TRACE_STORAGE_MAX_BYTES 限制，超限按创建时间淘汰最旧记录。
MESSAGE_TRACES_SCHEMA = """
CREATE TABLE message_traces (
    message_id TEXT PRIMARY KEY,
    trace_json TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (message_id)
        REFERENCES messages(message_id) ON DELETE CASCADE
)
"""

_SCHEMAS = (
    SCHEMA_META,
    DOCUMENTS_SCHEMA,
    DOCUMENT_UNITS_SCHEMA,
    CONVERSATIONS_SCHEMA,
    MESSAGES_SCHEMA,
    CHAT_REQUESTS_SCHEMA,
    TAGS_SCHEMA,
    DOCUMENT_TAGS_SCHEMA,
    MESSAGE_TRACES_SCHEMA,
)
_REQUIRED_TABLES = {
    "schema_meta",
    "documents",
    "document_units",
    "conversations",
    "messages",
    "chat_requests",
    "tags",
    "document_tags",
    "message_traces",
}
_V1_REQUIRED_TABLES = {
    "schema_meta",
    "documents",
    "document_units",
    "conversations",
    "messages",
    "chat_requests",
}
_V2_REQUIRED_TABLES = {
    "schema_meta",
    "documents",
    "document_units",
    "conversations",
    "messages",
    "chat_requests",
    "tags",
    "document_tags",
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


def _read_schema_version(connection: sqlite3.Connection) -> int:
    if "schema_meta" not in _user_tables(connection):
        raise UnsupportedSchemaError(
            "检测到未版本化 metadata 数据库；拒绝自动迁移或混写"
        )
    rows = connection.execute(
        "SELECT schema_version FROM schema_meta ORDER BY schema_version"
    ).fetchall()
    versions = [row[0] for row in rows]
    if len(versions) != 1:
        raise UnsupportedSchemaError(
            f"schema_meta 版本记录异常: {versions}"
        )
    return versions[0]


def _required_tables_for(version: int) -> set[str]:
    if version == 1:
        return _V1_REQUIRED_TABLES
    if version == 2:
        return _V2_REQUIRED_TABLES
    if version == SCHEMA_VERSION:
        return _REQUIRED_TABLES
    raise UnsupportedSchemaError(
        f"不支持的 metadata schema version: {version}"
    )


def _apply_migration(connection: sqlite3.Connection, from_version: int) -> None:
    """把 schema 从 from_version 迁移到 from_version + 1（保留性变更）。"""
    if from_version == 1:
        connection.execute(TAGS_SCHEMA)
        connection.execute(DOCUMENT_TAGS_SCHEMA)
        connection.execute(
            "UPDATE schema_meta SET schema_version = 2 WHERE schema_version = 1"
        )
        return
    if from_version == 2:
        connection.execute(MESSAGE_TRACES_SCHEMA)
        connection.execute(
            "UPDATE schema_meta SET schema_version = 3 WHERE schema_version = 2"
        )
        return
    raise UnsupportedSchemaError(
        f"缺少 schema {from_version} -> {from_version + 1} 的迁移"
    )


def _ensure_schema_current(
    connection: sqlite3.Connection,
    tables: set[str],
) -> None:
    """校验完整性并逐版本迁移到 SCHEMA_VERSION；由调用方负责事务边界。"""
    version = _read_schema_version(connection)
    while version < SCHEMA_VERSION:
        missing = _required_tables_for(version) - tables
        if missing:
            raise UnsupportedSchemaError(
                "metadata schema 不完整，缺少表: " + ", ".join(sorted(missing))
            )
        _apply_migration(connection, version)
        tables = _user_tables(connection)
        version += 1
    if version > SCHEMA_VERSION:
        raise UnsupportedSchemaError(
            f"不支持的 metadata schema version: {version}"
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
    """初始化空数据库；已有库必须能通过校验并被迁移到当前版本。"""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        tables = _user_tables(connection)
        if tables:
            connection.execute("BEGIN IMMEDIATE")
            try:
                _ensure_schema_current(connection, tables)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
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
    """读写结构化表前校验 schema 为当前版本；版本化旧库会被原地迁移。"""
    if not db_path:
        raise ValueError("结构化数据库校验必须提供明确 db_path")
    connection = sqlite3.connect(db_path, timeout=10)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        try:
            _ensure_schema_current(connection, _user_tables(connection))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    finally:
        connection.close()
