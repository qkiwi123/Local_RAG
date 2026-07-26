import importlib
import importlib.util
import sqlite3
from pathlib import Path

import pytest

from backend.models.document_structure import ParsedDocument, SourceLocator
from backend.services.chunker import chunk_units
from backend.services.document_store import create_document, mark_ready
from backend.services.metadata_schema import bootstrap_structured_database
from backend.services.parsers.assembler import BlockDraft, assemble_unit_from_drafts
from backend.services.parsers.constants import PARSER_VERSION
from backend.services.unit_store import list_document_units, replace_document_units


def _rebuild_module():
    spec = importlib.util.find_spec("scripts.rebuild_index")
    assert spec is not None
    return importlib.import_module("scripts.rebuild_index")


def _parsed_document(document_id: str, document_name: str) -> ParsedDocument:
    unit = assemble_unit_from_drafts(
        document_id=document_id,
        document_name=document_name,
        locator=SourceLocator(
            kind="section",
            unit_index=0,
            location_label="章节：测试",
            heading_path=["测试"],
        ),
        drafts=[
            BlockDraft(block_type="heading", text="测试", heading_level=1),
            BlockDraft(block_type="paragraph", text="ABCDEFGHIJ"),
        ],
    )
    return ParsedDocument(units=[unit])


def _fake_parse(path, document_name, document_id):
    return _parsed_document(document_id, document_name)


def test_load_ready_documents_only_returns_ready_rows(temp_dir):
    rebuild = _rebuild_module()
    db_path = Path(temp_dir) / "metadata.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE documents (
            document_id TEXT PRIMARY KEY,
            original_name TEXT NOT NULL,
            stored_name TEXT NOT NULL,
            status TEXT NOT NULL,
            chunk_count INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.executemany(
        "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("doc-ready", "ready.txt", "doc-ready.txt", "ready", 2, "2026-01-01"),
            ("doc-failed", "failed.txt", "doc-failed.txt", "failed", 0, "2026-01-02"),
        ],
    )
    connection.commit()
    connection.close()

    assert rebuild.load_ready_documents(db_path) == [
        {
            "document_id": "doc-ready",
            "original_name": "ready.txt",
            "stored_name": "doc-ready.txt",
            "chunk_count": 2,
        }
    ]


def test_prepare_documents_keeps_structured_units_and_checks_count(temp_dir):
    rebuild = _rebuild_module()
    source = Path(temp_dir) / "doc-1.md"
    source.write_text("# 测试", encoding="utf-8")
    records = [
        {
            "document_id": "doc-1",
            "original_name": "原始名称.md",
            "stored_name": source.name,
            "chunk_count": 1,
        }
    ]

    prepared = rebuild.prepare_documents(
        records,
        temp_dir,
        parse_document=_fake_parse,
        chunk_document=chunk_units,
        chunk_size=500,
        overlap=50,
    )

    assert prepared[0].record == records[0]
    assert prepared[0].parsed_units[0].parser_version == PARSER_VERSION
    assert prepared[0].chunks[0].metadata.unit_id == prepared[0].parsed_units[0].unit_id

    records[0]["chunk_count"] = 2
    with pytest.raises(RuntimeError, match="登记为 2.*实际生成 1"):
        rebuild.prepare_documents(
            records,
            temp_dir,
            parse_document=_fake_parse,
            chunk_document=chunk_units,
            chunk_size=500,
            overlap=50,
        )


def test_prepare_documents_rejects_missing_file_or_corrupt_chunk(temp_dir):
    rebuild = _rebuild_module()
    record = {
        "document_id": "doc-1",
        "original_name": "test.md",
        "stored_name": "missing.md",
        "chunk_count": 1,
    }
    with pytest.raises(FileNotFoundError):
        rebuild.prepare_documents(
            [record],
            temp_dir,
            parse_document=_fake_parse,
            chunk_document=chunk_units,
            chunk_size=500,
            overlap=50,
        )

    source = Path(temp_dir) / "test.md"
    source.write_text("# 测试", encoding="utf-8")
    record["stored_name"] = source.name

    def corrupt_chunk(units, chunk_size, overlap, **kwargs):
        chunk = chunk_units(units, chunk_size, overlap, **kwargs)[0]
        return [chunk.model_copy(update={"content": "被篡改"})]

    with pytest.raises(ValueError, match="切片不一致"):
        rebuild.prepare_documents(
            [record],
            temp_dir,
            parse_document=_fake_parse,
            chunk_document=corrupt_chunk,
            chunk_size=500,
            overlap=50,
        )


def test_ensure_safe_target_rejects_source_or_nonempty_directory(temp_dir):
    rebuild = _rebuild_module()
    source = Path(temp_dir) / "old-index"
    source.mkdir()
    with pytest.raises(ValueError, match="不能与旧索引相同"):
        rebuild.ensure_safe_target(source, source)

    target = Path(temp_dir) / "new-index"
    target.mkdir()
    (target / "chroma.sqlite3").write_text("occupied", encoding="utf-8")
    with pytest.raises(FileExistsError, match="目标目录非空"):
        rebuild.ensure_safe_target(source, target)


class _FakeCollection:
    def __init__(self, embeddings, metadatas):
        self._embeddings = embeddings
        self._metadatas = metadatas

    def count(self):
        return len(self._metadatas)

    def get(self, include):
        assert include == ["embeddings", "metadatas"]
        return {
            "embeddings": self._embeddings,
            "metadatas": self._metadatas,
        }


def test_verify_collection_checks_counts_ids_and_dimension():
    rebuild = _rebuild_module()
    collection = _FakeCollection(
        embeddings=[[0.0] * 1024 for _ in range(3)],
        metadatas=[
            {"document_id": "doc-1"},
            {"document_id": "doc-1"},
            {"document_id": "doc-2"},
        ],
    )
    assert rebuild.verify_collection(
        collection,
        expected_counts={"doc-1": 2, "doc-2": 1},
        expected_dimension=1024,
    )["chunk_count"] == 3

    with pytest.raises(RuntimeError, match="向量维度"):
        rebuild.verify_collection(
            collection,
            expected_counts={"doc-1": 2, "doc-2": 1},
            expected_dimension=384,
        )


def test_checked_write_units_runs_only_after_collection_verification(temp_dir):
    rebuild = _rebuild_module()
    db_path = Path(temp_dir) / "metadata.db"
    bootstrap_structured_database(
        str(db_path),
        created_at="2026-07-26T00:00:00+00:00",
    )
    create_document("doc-1", "test.md", "doc-1.md", 10, str(db_path))
    parsed = _parsed_document("doc-1", "test.md")
    chunks = chunk_units(parsed.units, 500, 50)
    mark_ready("doc-1", len(chunks), str(db_path))
    prepared = [
        rebuild.PreparedDocument(
            record={"document_id": "doc-1"},
            chunks=chunks,
            parsed_units=parsed.units,
            warnings=[],
        )
    ]

    bad = _FakeCollection(
        embeddings=[[0.0] * 1024],
        metadatas=[{"document_id": "other"}],
    )
    with pytest.raises(RuntimeError, match="document_id"):
        rebuild._checked_write_document_units(
            prepared,
            db_path,
            expected_counts={"doc-1": 1},
            expected_dimension=1024,
            _fake_collection=bad,
        )
    assert list_document_units(
        "doc-1",
        PARSER_VERSION,
        db_path=str(db_path),
    ) == []

    good = _FakeCollection(
        embeddings=[[0.0] * 1024],
        metadatas=[{"document_id": "doc-1"}],
    )
    rebuild._checked_write_document_units(
        prepared,
        db_path,
        expected_counts={"doc-1": 1},
        expected_dimension=1024,
        _fake_collection=good,
    )
    assert list_document_units(
        "doc-1",
        PARSER_VERSION,
        db_path=str(db_path),
    ) == parsed.units


def test_update_document_chunk_counts_is_transactional(temp_dir):
    db_path = Path(temp_dir) / "metadata.db"
    bootstrap_structured_database(
        str(db_path),
        created_at="2026-07-26T00:00:00+00:00",
    )
    for document_id in ("doc-1", "doc-2"):
        create_document(
            document_id,
            f"{document_id}.txt",
            f"{document_id}.txt",
            10,
            str(db_path),
        )
        mark_ready(document_id, 1, str(db_path))

    rebuild = _rebuild_module()
    rebuild.update_document_chunk_counts(db_path, {"doc-1": 3, "doc-2": 4})
    connection = sqlite3.connect(db_path)
    rows = dict(
        connection.execute(
            "SELECT document_id, chunk_count FROM documents ORDER BY document_id"
        ).fetchall()
    )
    connection.close()
    assert rows == {"doc-1": 3, "doc-2": 4}

    with pytest.raises(RuntimeError, match="missing-doc"):
        rebuild.update_document_chunk_counts(
            db_path,
            {"doc-1": 9, "missing-doc": 1},
        )
    connection = sqlite3.connect(db_path)
    count = connection.execute(
        "SELECT chunk_count FROM documents WHERE document_id = 'doc-1'"
    ).fetchone()[0]
    connection.close()
    assert count == 3
