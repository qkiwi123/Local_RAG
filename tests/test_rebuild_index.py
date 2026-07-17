import importlib
import importlib.util
import sqlite3
from pathlib import Path

import pytest


def _rebuild_module():
    spec = importlib.util.find_spec("scripts.rebuild_index")
    assert spec is not None, "缺少可复用的 scripts.rebuild_index 索引重建入口"
    return importlib.import_module("scripts.rebuild_index")


def test_load_ready_documents_only_returns_ready_rows(tmp_path):
    rebuild = _rebuild_module()
    db_path = tmp_path / "metadata.db"
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
        """
        INSERT INTO documents (
            document_id, original_name, stored_name, status, chunk_count, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            ("doc-ready", "ready.txt", "doc-ready.txt", "ready", 2, "2026-01-01"),
            ("doc-failed", "failed.txt", "doc-failed.txt", "failed", 0, "2026-01-02"),
        ],
    )
    connection.commit()
    connection.close()

    records = rebuild.load_ready_documents(db_path)

    assert records == [
        {
            "document_id": "doc-ready",
            "original_name": "ready.txt",
            "stored_name": "doc-ready.txt",
            "chunk_count": 2,
        }
    ]


def test_prepare_documents_preserves_ids_and_checks_chunk_count(tmp_path):
    rebuild = _rebuild_module()
    source = tmp_path / "doc-1.txt"
    source.write_text("测试内容", encoding="utf-8")
    records = [
        {
            "document_id": "doc-1",
            "original_name": "原始名称.txt",
            "stored_name": source.name,
            "chunk_count": 2,
        }
    ]
    calls = {}

    def fake_parse(path, document_name, document_id):
        calls["parse"] = (path, document_name, document_id)
        return [{"text": "测试内容"}]

    def fake_chunk(pages, chunk_size, overlap):
        calls["chunk"] = (pages, chunk_size, overlap)
        return ["chunk-1", "chunk-2"]

    prepared = rebuild.prepare_documents(
        records,
        tmp_path,
        parse_document=fake_parse,
        chunk_document=fake_chunk,
        chunk_size=500,
        overlap=50,
    )

    assert prepared == [(records[0], ["chunk-1", "chunk-2"])]
    assert calls["parse"] == (str(source), "原始名称.txt", "doc-1")
    assert calls["chunk"] == ([{"text": "测试内容"}], 500, 50)

    records[0]["chunk_count"] = 3
    with pytest.raises(RuntimeError, match="doc-1.*登记为 3.*实际生成 2"):
        rebuild.prepare_documents(
            records,
            tmp_path,
            parse_document=fake_parse,
            chunk_document=fake_chunk,
            chunk_size=500,
            overlap=50,
        )


def test_ensure_safe_target_rejects_source_or_nonempty_directory(tmp_path):
    rebuild = _rebuild_module()
    source = tmp_path / "old-index"
    source.mkdir()

    with pytest.raises(ValueError, match="不能与旧索引相同"):
        rebuild.ensure_safe_target(source, source)

    target = tmp_path / "new-index"
    target.mkdir()
    (target / "chroma.sqlite3").write_text("occupied", encoding="utf-8")
    with pytest.raises(FileExistsError, match="目标目录非空"):
        rebuild.ensure_safe_target(source, target)

    rebuild.ensure_safe_target(source, tmp_path / "unused-index")


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


def test_verify_collection_checks_counts_document_ids_and_dimension():
    rebuild = _rebuild_module()
    collection = _FakeCollection(
        embeddings=[[0.0] * 1024 for _ in range(3)],
        metadatas=[
            {"document_id": "doc-1"},
            {"document_id": "doc-1"},
            {"document_id": "doc-2"},
        ],
    )

    summary = rebuild.verify_collection(
        collection,
        expected_counts={"doc-1": 2, "doc-2": 1},
        expected_dimension=1024,
    )

    assert summary == {
        "chunk_count": 3,
        "document_count": 2,
        "embedding_dimension": 1024,
        "chunks_by_document": {"doc-1": 2, "doc-2": 1},
    }

    with pytest.raises(RuntimeError, match="向量维度.*1024.*384"):
        rebuild.verify_collection(
            collection,
            expected_counts={"doc-1": 2, "doc-2": 1},
            expected_dimension=384,
        )
