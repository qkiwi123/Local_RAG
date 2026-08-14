import sqlite3

import pytest

from backend.services.metadata_schema import (
    SCHEMA_VERSION,
    UnsupportedSchemaError,
    bootstrap_structured_database,
)


CREATED_AT = "2026-07-26T00:00:00+00:00"


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
