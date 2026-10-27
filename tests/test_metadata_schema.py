import sqlite3

import pytest

from backend.services.metadata_schema import (
    SCHEMA_VERSION,
    UnsupportedSchemaError,
    bootstrap_structured_database,
)


CREATED_AT = "2026-07-26T00:00:00+00:00"


def test_v5_migration_preserves_messages_and_marks_scope_unknown(temp_dir):
    from backend.services import metadata_schema as schema
    db_path = f"{temp_dir}/v5.db"
    with sqlite3.connect(db_path) as connection:
        for ddl in schema._SCHEMAS:
            if ddl != schema.CHAT_REPLACEMENTS_SCHEMA:
                connection.execute(ddl.replace("    retrieval_tags_json TEXT,\n", ""))
        connection.execute("INSERT INTO schema_meta VALUES (5, ?)", (CREATED_AT,))
        connection.execute("INSERT INTO conversations VALUES ('c1','旧会话',?,?)", (CREATED_AT, CREATED_AT))
        connection.execute("""INSERT INTO messages (message_id,conversation_id,request_id,sequence,role,content,status,created_at)
            VALUES ('m1','c1','r1',0,'user','旧问题','complete',?)""", (CREATED_AT,))
    bootstrap_structured_database(db_path, created_at="upgrade")
    bootstrap_structured_database(db_path, created_at="upgrade again")
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT content, retrieval_tags_json FROM messages").fetchall() == [("旧问题", None)]
        assert connection.execute("SELECT schema_version FROM schema_meta").fetchone()[0] == 6


def test_v4_migration_preserves_old_messages_and_defaults_reasoning_to_empty(temp_dir):
    from backend.services import metadata_schema as schema
    db_path = f"{temp_dir}/v4.db"
    with sqlite3.connect(db_path) as connection:
        for ddl in schema._SCHEMAS:
            if ddl == schema.CHAT_REPLACEMENTS_SCHEMA:
                continue
            connection.execute(ddl.replace("    reasoning_content TEXT NOT NULL DEFAULT '',\n", "")
                               .replace("    retrieval_tags_json TEXT,\n", ""))
        connection.execute("INSERT INTO schema_meta VALUES (4, ?)", (CREATED_AT,))
        connection.execute("INSERT INTO conversations VALUES ('c1', '旧会话', ?, ?)", (CREATED_AT, CREATED_AT))
        connection.execute("INSERT INTO messages VALUES ('m1','c1','r1',0,'assistant','旧回答','complete',NULL,'[]',?)", (CREATED_AT,))
    bootstrap_structured_database(db_path, created_at=CREATED_AT)
    bootstrap_structured_database(db_path, created_at=CREATED_AT)
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT content, reasoning_content FROM messages").fetchone() == ("旧回答", "")
        assert connection.execute("SELECT schema_version FROM schema_meta").fetchone()[0] == SCHEMA_VERSION


def _tables(db_path: str) -> set[str]:
    connection = sqlite3.connect(db_path)
    try:
        return {
            row[0]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                """
            )
        }
    finally:
        connection.close()


def test_empty_database_bootstraps_complete_versioned_schema(temp_dir):
    db_path = f"{temp_dir}/metadata.db"

    bootstrap_structured_database(db_path, created_at=CREATED_AT)

    assert _tables(db_path) == {
        "schema_meta",
        "documents",
        "document_units",
        "conversations",
        "messages",
        "chat_requests",
        "tags",
        "document_tags",
        "message_traces",
        "runtime_settings",
        "runtime_settings_history",
        "chat_replacements",
    }
    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute(
            "SELECT schema_version, created_at FROM schema_meta"
        ).fetchall() == [(SCHEMA_VERSION, CREATED_AT)]
    finally:
        connection.close()


def test_bootstrap_is_idempotent_for_supported_complete_schema(temp_dir):
    db_path = f"{temp_dir}/metadata.db"
    bootstrap_structured_database(db_path, created_at=CREATED_AT)

    bootstrap_structured_database(
        db_path,
        created_at="later timestamp must not replace the original",
    )

    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute(
            "SELECT created_at FROM schema_meta"
        ).fetchone()[0] == CREATED_AT
    finally:
        connection.close()


def test_unversioned_existing_database_is_rejected_without_mutation(temp_dir):
    db_path = f"{temp_dir}/metadata.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("CREATE TABLE documents (document_id TEXT)")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(UnsupportedSchemaError, match="未版本化"):
        bootstrap_structured_database(db_path, created_at=CREATED_AT)

    assert _tables(db_path) == {"documents"}


def test_unsupported_or_incomplete_schema_is_rejected(temp_dir):
    unsupported_path = f"{temp_dir}/unsupported.db"
    connection = sqlite3.connect(unsupported_path)
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
        bootstrap_structured_database(unsupported_path, created_at=CREATED_AT)

    incomplete_path = f"{temp_dir}/incomplete.db"
    connection = sqlite3.connect(incomplete_path)
    try:
        connection.execute(
            "CREATE TABLE schema_meta (schema_version INTEGER PRIMARY KEY, created_at TEXT)"
        )
        connection.execute(
            "INSERT INTO schema_meta VALUES (?, ?)",
            (SCHEMA_VERSION, CREATED_AT),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(UnsupportedSchemaError, match="不完整"):
        bootstrap_structured_database(incomplete_path, created_at=CREATED_AT)
