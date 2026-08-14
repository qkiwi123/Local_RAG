"""schema v1 -> v2 显式迁移测试：保留业务数据，新增标签表，幂等可重复执行。"""

import sqlite3

import pytest

from backend.services.metadata_schema import (
    SCHEMA_VERSION,
    UnsupportedSchemaError,
    bootstrap_structured_database,
    require_structured_database,
)

CREATED_AT = "2026-07-26T00:00:00+00:00"

_V1_TABLES = {
    "schema_meta": """
        CREATE TABLE schema_meta (
            schema_version INTEGER PRIMARY KEY,
            created_at TEXT NOT NULL
        )
    """,
    "documents": """
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
    """,
    "document_units": """
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
    """,
    "conversations": """
        CREATE TABLE conversations (
            conversation_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """,
    "messages": """
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
    """,
    "chat_requests": """
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
    """,
}


def _create_v1_database(db_path: str) -> None:
    """按 v1 schema 建库并写入一条业务数据，模拟存量生产库。"""
    connection = sqlite3.connect(db_path)
    try:
        for schema in _V1_TABLES.values():
            connection.execute(schema)
        connection.execute(
            "INSERT INTO schema_meta (schema_version, created_at) VALUES (1, ?)",
            (CREATED_AT,),
        )
        connection.execute(
            """
            INSERT INTO documents (
                document_id, original_name, stored_name, size_bytes,
                status, chunk_count, error, created_at, updated_at
            ) VALUES ('doc-1', '手册.pdf', 'doc-1.pdf', 10, 'ready', 3,
                      NULL, ?, ?)
            """,
            (CREATED_AT, CREATED_AT),
        )
        connection.commit()
    finally:
        connection.close()


def test_bootstrap_migrates_v1_database_and_preserves_business_rows(temp_dir):
    db_path = f"{temp_dir}/metadata.db"
    _create_v1_database(db_path)

    bootstrap_structured_database(db_path, created_at="new timestamp")

    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute(
            "SELECT schema_version FROM schema_meta"
        ).fetchone()[0] == SCHEMA_VERSION
        assert connection.execute(
            "SELECT original_name FROM documents WHERE document_id = 'doc-1'"
        ).fetchone()[0] == "手册.pdf"
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"tags", "document_tags"} <= tables
    finally:
        connection.close()


def test_bootstrap_migration_is_idempotent(temp_dir):
    db_path = f"{temp_dir}/metadata.db"
    _create_v1_database(db_path)

    bootstrap_structured_database(db_path, created_at=CREATED_AT)
    bootstrap_structured_database(db_path, created_at="later must not matter")

    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute(
            "SELECT schema_version FROM schema_meta"
        ).fetchone()[0] == SCHEMA_VERSION
        assert connection.execute(
            "SELECT created_at FROM schema_meta"
        ).fetchone()[0] == CREATED_AT
        # 迁移只插入一次，tags 表无重复结构异常
        assert connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 0
    finally:
        connection.close()


def test_require_structured_database_migrates_v1_in_place(temp_dir):
    """unit_store 等读写路径直接调用 require_structured_database，也必须迁移。"""
    db_path = f"{temp_dir}/metadata.db"
    _create_v1_database(db_path)

    require_structured_database(db_path)

    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute(
            "SELECT schema_version FROM schema_meta"
        ).fetchone()[0] == SCHEMA_VERSION
    finally:
        connection.close()


def test_unsupported_newer_version_still_rejected(temp_dir):
    db_path = f"{temp_dir}/metadata.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "CREATE TABLE schema_meta (schema_version INTEGER PRIMARY KEY, created_at TEXT)"
        )
        connection.execute(
            "INSERT INTO schema_meta VALUES (?, ?)",
            (SCHEMA_VERSION + 1, CREATED_AT),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(UnsupportedSchemaError, match="不支持"):
        bootstrap_structured_database(db_path, created_at=CREATED_AT)


def test_incomplete_v1_database_is_rejected_without_migration(temp_dir):
    db_path = f"{temp_dir}/metadata.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(_V1_TABLES["schema_meta"])
        connection.execute(_V1_TABLES["documents"])
        connection.execute(
            "INSERT INTO schema_meta (schema_version, created_at) VALUES (1, ?)",
            (CREATED_AT,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(UnsupportedSchemaError, match="不完整"):
        bootstrap_structured_database(db_path, created_at=CREATED_AT)


_V2_EXTRA_TABLES = {
    "tags": """
        CREATE TABLE tags (
            tag_path TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """,
    "document_tags": """
        CREATE TABLE document_tags (
            document_id TEXT NOT NULL,
            tag_path TEXT NOT NULL,
            PRIMARY KEY (document_id, tag_path),
            FOREIGN KEY (document_id)
                REFERENCES documents(document_id) ON DELETE CASCADE,
            FOREIGN KEY (tag_path)
                REFERENCES tags(tag_path) ON DELETE CASCADE
        )
    """,
}


def _create_v2_database(db_path: str) -> None:
    """按 v2 schema 建库并写入会话数据，模拟升级前的生产库。"""
    _create_v1_database(db_path)
    connection = sqlite3.connect(db_path)
    try:
        for schema in _V2_EXTRA_TABLES.values():
            connection.execute(schema)
        connection.execute(
            "UPDATE schema_meta SET schema_version = 2 WHERE schema_version = 1"
        )
        connection.execute(
            """
            INSERT INTO conversations (conversation_id, title, created_at, updated_at)
            VALUES ('conv-1', '旧会话', ?, ?)
            """,
            (CREATED_AT, CREATED_AT),
        )
        connection.commit()
    finally:
        connection.close()


def test_bootstrap_migrates_v2_to_v3_and_preserves_rows(temp_dir):
    db_path = f"{temp_dir}/metadata.db"
    _create_v2_database(db_path)

    bootstrap_structured_database(db_path, created_at="new timestamp")

    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute(
            "SELECT schema_version FROM schema_meta"
        ).fetchone()[0] == SCHEMA_VERSION
        assert connection.execute(
            "SELECT title FROM conversations WHERE conversation_id = 'conv-1'"
        ).fetchone()[0] == "旧会话"
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "message_traces" in tables
    finally:
        connection.close()


def test_v2_migration_is_idempotent(temp_dir):
    db_path = f"{temp_dir}/metadata.db"
    _create_v2_database(db_path)

    bootstrap_structured_database(db_path, created_at=CREATED_AT)
    bootstrap_structured_database(db_path, created_at="later")

    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute(
            "SELECT schema_version FROM schema_meta"
        ).fetchone()[0] == SCHEMA_VERSION
    finally:
        connection.close()
