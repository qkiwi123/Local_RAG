import importlib
import importlib.util
import sqlite3
from pathlib import Path

import pytest


def _rebuild_module():
    spec = importlib.util.find_spec("scripts.rebuild_index")
    assert spec is not None, "缺少可复用的 scripts.rebuild_index 索引重建入口"
    return importlib.import_module("scripts.rebuild_index")


# ── helpers ──

def _fake_parse_single_page(path, document_name, document_id):
    """返回单页单 block 的解析结果。"""
    return [{
        "text": "ABCDEFGHIJ",
        "page_number": 1,
        "document_name": document_name,
        "document_id": document_id,
        "content_type": "paragraph",
        "parser_version": "text-v1-pypdf2",
    }]


def _fake_chunk_fixed(pages, chunk_size, overlap, **kwargs):
    """固定大小切分，模拟真实 chunker 的 offset 语义。"""
    from backend.models.schemas import DocumentChunk, ChunkMetadata

    chunks = []
    for page in pages:
        text = page.get("text", "")
        doc_id = page.get("document_id", "")
        doc_name = page.get("document_name", "unknown")
        page_num = page.get("page_number", 1)
        ctype = page.get("content_type", "paragraph")
        pv = page.get("parser_version", "")

        start = 0
        ci = 0
        step = chunk_size - overlap
        while start < len(text):
            end = min(start + chunk_size, len(text))
            chunk_text = text[start:end]
            if not chunk_text.strip():
                start += step
                continue
            stable_id = f"{doc_id}_p{page_num}_b0_s{start}_e{end}"
            chunks.append(DocumentChunk(
                content=chunk_text,
                metadata=ChunkMetadata(
                    document_id=doc_id,
                    document_name=doc_name,
                    page_number=page_num,
                    chunk_index=ci,
                    content_type=ctype,
                    chunk_id=stable_id,
                    block_index=0,
                    start_char=start,
                    end_char=end,
                    char_count=len(chunk_text),
                    token_count=len(chunk_text),
                    parser_version=pv,
                ),
            ))
            ci += 1
            start += step
    return chunks


def test_load_ready_documents_only_returns_ready_rows(temp_dir):
    from pathlib import Path
    tmp = Path(temp_dir)

    rebuild = _rebuild_module()
    db_path = tmp / "metadata.db"
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


def test_prepare_documents_preserves_ids_and_checks_chunk_count(temp_dir):
    from pathlib import Path
    tmp = Path(temp_dir)

    rebuild = _rebuild_module()
    source = tmp / "doc-1.txt"
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
        return [{
            "text": "测试内容",
            "document_id": document_id,
            "document_name": document_name,
            "page_number": 1,
            "content_type": "paragraph",
            "parser_version": "text-v1-pypdf2",
        }]

    def fake_chunk(pages, chunk_size, overlap, **kwargs):
        calls["chunk"] = (pages, chunk_size, overlap)
        from backend.models.schemas import DocumentChunk, ChunkMetadata
        return [
            DocumentChunk(
                content="测试",
                metadata=ChunkMetadata(
                    document_id="doc-1", document_name="原始名称.txt",
                    page_number=1, chunk_index=0, content_type="paragraph",
                    chunk_id="doc-1_p1_b0_s0_e2", block_index=0,
                    start_char=0, end_char=2, parser_version="text-v1-pypdf2",
                ),
            ),
            DocumentChunk(
                content="内容",
                metadata=ChunkMetadata(
                    document_id="doc-1", document_name="原始名称.txt",
                    page_number=1, chunk_index=1, content_type="paragraph",
                    chunk_id="doc-1_p1_b0_s2_e4", block_index=0,
                    start_char=2, end_char=4, parser_version="text-v1-pypdf2",
                ),
            ),
        ]

    prepared = rebuild.prepare_documents(
        records,
        tmp,
        parse_document=fake_parse,
        chunk_document=fake_chunk,
        chunk_size=500,
        overlap=50,
    )

    assert prepared[0].record == records[0]
    assert len(prepared[0].chunks) == 2
    assert prepared[0].chunks[0].content == "测试"
    assert prepared[0].chunks[1].content == "内容"
    assert calls["parse"] == (str(source), "原始名称.txt", "doc-1")
    # verify chunk was called with parsed pages (now enriched with metadata)
    assert calls["chunk"][1:] == (500, 50)

    records[0]["chunk_count"] = 3
    with pytest.raises(RuntimeError, match="doc-1.*登记为 3.*实际生成 2"):
        rebuild.prepare_documents(
            records,
            tmp,
            parse_document=fake_parse,
            chunk_document=fake_chunk,
            chunk_size=500,
            overlap=50,
        )

    prepared = rebuild.prepare_documents(
        records,
        tmp,
        parse_document=fake_parse,
        chunk_document=fake_chunk,
        chunk_size=500,
        overlap=50,
        enforce_registered_counts=False,
    )
    assert prepared[0].record == records[0]
    assert len(prepared[0].chunks) == 2


def test_ensure_safe_target_rejects_source_or_nonempty_directory(temp_dir):
    from pathlib import Path
    tmp = Path(temp_dir)

    rebuild = _rebuild_module()
    source = tmp / "old-index"
    source.mkdir()

    with pytest.raises(ValueError, match="不能与旧索引相同"):
        rebuild.ensure_safe_target(source, source)

    target = tmp / "new-index"
    target.mkdir()
    (target / "chroma.sqlite3").write_text("occupied", encoding="utf-8")
    with pytest.raises(FileExistsError, match="目标目录非空"):
        rebuild.ensure_safe_target(source, target)

    rebuild.ensure_safe_target(source, tmp / "unused-index")


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


def test_update_document_chunk_counts_is_transactional(temp_dir):
    from pathlib import Path
    tmp = Path(temp_dir)

    rebuild = _rebuild_module()
    db_path = tmp / "metadata.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE documents (
            document_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            chunk_count INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.executemany(
        "INSERT INTO documents VALUES (?, 'ready', ?, 'old')",
        [("doc-1", 2), ("doc-2", 1)],
    )
    connection.commit()
    connection.close()

    rebuild.update_document_chunk_counts(db_path, {"doc-1": 3, "doc-2": 4})

    connection = sqlite3.connect(db_path)
    rows = dict(connection.execute(
        "SELECT document_id, chunk_count FROM documents ORDER BY document_id"
    ).fetchall())
    connection.close()
    assert rows == {"doc-1": 3, "doc-2": 4}

    with pytest.raises(RuntimeError, match="missing-doc"):
        rebuild.update_document_chunk_counts(
            db_path,
            {"doc-1": 9, "missing-doc": 1},
        )

    connection = sqlite3.connect(db_path)
    doc_1_count = connection.execute(
        "SELECT chunk_count FROM documents WHERE document_id = 'doc-1'"
    ).fetchone()[0]
    connection.close()
    assert doc_1_count == 3


# ── Task 9: 扩展重建脚本，补齐页快照 ──


class TestPrepareDocumentsReturnsParsedPages:
    """prepare_documents() 现需同时产出 chunks 和 ParsedPage。"""

    def test_prepared_document_has_chunks_and_pages(self, temp_dir):
        """PreparedDocument 包含 record、chunks 和解析出的 ParsedPage 列表。"""
        from pathlib import Path
        tmp = Path(temp_dir)

        rebuild = _rebuild_module()
        source = tmp / "doc.txt"
        source.write_text("ABCDEFGHIJ", encoding="utf-8")
        records = [{
            "document_id": "doc-1",
            "original_name": "test.txt",
            "stored_name": source.name,
            "chunk_count": 3,  # chunk_size=5, overlap=1 on 10 chars → 3 chunks
        }]

        prepared = rebuild.prepare_documents(
            records,
            tmp,
            parse_document=_fake_parse_single_page,
            chunk_document=_fake_chunk_fixed,
            chunk_size=5,
            overlap=1,
        )

        assert len(prepared) == 1
        pd = prepared[0]
        assert pd.record == records[0]
        assert len(pd.chunks) == 3
        assert len(pd.parsed_pages) == 1
        from backend.services.source_page import ParsedPage
        assert isinstance(pd.parsed_pages[0], ParsedPage)
        assert pd.parsed_pages[0].parsed_page_text == "ABCDEFGHIJ"

    def test_chunk_page_validation_during_prepare(self, temp_dir):
        """prepare 阶段即验证所有 chunk/page 切片，失败时终止。"""
        from pathlib import Path
        tmp = Path(temp_dir)

        rebuild = _rebuild_module()
        source = tmp / "doc.txt"
        source.write_text("ABCDEFGHIJ", encoding="utf-8")
        records = [{
            "document_id": "doc-1",
            "original_name": "test.txt",
            "stored_name": source.name,
            "chunk_count": 1,  # broken_chunk 只返回 1 个 chunk
        }]

        def broken_chunk(pages, chunk_size, overlap, **kwargs):
            from backend.models.schemas import DocumentChunk, ChunkMetadata
            return [DocumentChunk(
                content="WRONG",
                metadata=ChunkMetadata(
                    document_id="doc-1", document_name="t", page_number=1,
                    chunk_index=0, content_type="paragraph",
                    chunk_id="x", start_char=0, end_char=5,
                    parser_version="text-v1-pypdf2",
                ),
            )]

        with pytest.raises(ValueError, match="切片不一致"):
            rebuild.prepare_documents(
                records,
                tmp,
                parse_document=_fake_parse_single_page,
                chunk_document=broken_chunk,
                chunk_size=5,
                overlap=1,
            )


class TestPageSnapshotsWrittenAfterChromaVerification:
    """页快照只在 Chroma 验证成功后写入；失败时页表不变。"""

    def test_page_table_unchanged_when_collection_verification_fails(self, temp_dir):
        """目标 Chroma 校验失败时，页表完全不变。"""
        from pathlib import Path
        tmp = Path(temp_dir)

        rebuild = _rebuild_module()
        source = tmp / "doc.txt"
        source.write_text("ABCDEFGHIJ", encoding="utf-8")
        records = [{
            "document_id": "doc-1",
            "original_name": "test.txt",
            "stored_name": source.name,
            "chunk_count": 3,  # chunk_size=5, overlap=1 on 10 chars
        }]

        prepared = rebuild.prepare_documents(
            records,
            tmp,
            parse_document=_fake_parse_single_page,
            chunk_document=_fake_chunk_fixed,
            chunk_size=5,
            overlap=1,
        )

        db_path = str(tmp / "meta.db")
        from backend.services.document_store import _SCHEMA
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(_SCHEMA)
        conn.execute(
            "INSERT INTO documents VALUES (?, ?, ?, 0, 'ready', 2, NULL, ?, ?)",
            ("doc-1", "test.txt", source.name, "2026-01-01", "2026-01-01"),
        )
        conn.commit()
        conn.close()

        from backend.services.page_store import _ensure_page_schema
        _ensure_page_schema(db_path)

        # 先写入一些旧页数据
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO document_pages VALUES (?, ?, ?, ?, ?, ?)",
            ("doc-1", "old-version", 1, "OLD PAGE", "paragraph", "2026-01-01"),
        )
        conn.commit()
        conn.close()

        # 用错误的 expected_counts 触发 verify_collection 失败
        with pytest.raises(RuntimeError, match="索引总块数"):
            rebuild._checked_write_page_snapshots(
                prepared,
                db_path,
                expected_counts={"doc-1": 999},  # 故意错误
                expected_dimension=1024,
                _fake_collection_count=3,
                _fake_collection_metadatas=[
                    {"document_id": "doc-1"},
                    {"document_id": "doc-1"},
                    {"document_id": "doc-1"},
                ],
                _fake_collection_embeddings=[[0.0] * 1024 for _ in range(3)],
            )

        # 页表不应写入新版本
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM document_pages ORDER BY parser_version"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]["parser_version"] == "old-version"

    def test_page_snapshots_written_after_successful_verification(self, temp_dir):
        """目标 Chroma 验证成功后，才事务写入页快照。"""
        from pathlib import Path
        tmp = Path(temp_dir)

        rebuild = _rebuild_module()
        source = tmp / "doc.txt"
        source.write_text("ABCDEFGHIJ", encoding="utf-8")
        records = [{
            "document_id": "doc-1",
            "original_name": "test.txt",
            "stored_name": source.name,
            "chunk_count": 3,  # chunk_size=5, overlap=1 on 10 chars
        }]

        prepared = rebuild.prepare_documents(
            records,
            tmp,
            parse_document=_fake_parse_single_page,
            chunk_document=_fake_chunk_fixed,
            chunk_size=5,
            overlap=1,
        )

        db_path = str(tmp / "meta.db")
        from backend.services.document_store import _SCHEMA
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(_SCHEMA)
        conn.execute(
            "INSERT INTO documents VALUES (?, ?, ?, 0, 'ready', 2, NULL, ?, ?)",
            ("doc-1", "test.txt", source.name, "2026-01-01", "2026-01-01"),
        )
        conn.commit()
        conn.close()

        from backend.services.page_store import _ensure_page_schema
        _ensure_page_schema(db_path)

        rebuild._checked_write_page_snapshots(
            prepared,
            db_path,
            expected_counts={"doc-1": 3},
            expected_dimension=1024,
            _fake_collection_count=3,
            _fake_collection_metadatas=[
                {"document_id": "doc-1"},
                {"document_id": "doc-1"},
                {"document_id": "doc-1"},
            ],
            _fake_collection_embeddings=[[0.0] * 1024 for _ in range(3)],
        )

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM document_pages WHERE parser_version = 'text-v1-pypdf2'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]["parsed_page_text"] == "ABCDEFGHIJ"
        assert rows[0]["document_id"] == "doc-1"

    def test_old_parser_version_pages_not_deleted(self, temp_dir):
        """写入新 parser 版本不会误删旧版本的页快照。"""
        from pathlib import Path
        tmp = Path(temp_dir)

        rebuild = _rebuild_module()
        source = tmp / "doc.txt"
        source.write_text("ABCDEFGHIJ", encoding="utf-8")
        records = [{
            "document_id": "doc-1",
            "original_name": "test.txt",
            "stored_name": source.name,
            "chunk_count": 3,  # chunk_size=5, overlap=1 on 10 chars
        }]

        prepared = rebuild.prepare_documents(
            records,
            tmp,
            parse_document=_fake_parse_single_page,
            chunk_document=_fake_chunk_fixed,
            chunk_size=5,
            overlap=1,
        )

        db_path = str(tmp / "meta.db")
        from backend.services.document_store import _SCHEMA
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(_SCHEMA)
        conn.execute(
            "INSERT INTO documents VALUES (?, ?, ?, 0, 'ready', 2, NULL, ?, ?)",
            ("doc-1", "test.txt", source.name, "2026-01-01", "2026-01-01"),
        )
        conn.commit()
        conn.close()

        from backend.services.page_store import _ensure_page_schema
        _ensure_page_schema(db_path)

        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO document_pages VALUES (?, ?, ?, ?, ?, ?)",
            ("doc-1", "text-v0-legacy", 1, "OLD PAGE", "paragraph", "2026-01-01"),
        )
        conn.commit()
        conn.close()

        rebuild._checked_write_page_snapshots(
            prepared,
            db_path,
            expected_counts={"doc-1": 3},
            expected_dimension=1024,
            _fake_collection_count=3,
            _fake_collection_metadatas=[
                {"document_id": "doc-1"},
                {"document_id": "doc-1"},
                {"document_id": "doc-1"},
            ],
            _fake_collection_embeddings=[[0.0] * 1024 for _ in range(3)],
        )

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM document_pages ORDER BY parser_version"
        ).fetchall()
        conn.close()
        assert len(rows) == 2
        versions = {row["parser_version"] for row in rows}
        assert "text-v0-legacy" in versions
        assert "text-v1-pypdf2" in versions

    def test_missing_source_file_leaves_page_table_unchanged(self, temp_dir):
        """源文件缺失时页表不变。"""
        from pathlib import Path
        tmp = Path(temp_dir)

        rebuild = _rebuild_module()
        records = [{
            "document_id": "doc-1",
            "original_name": "test.txt",
            "stored_name": "nonexistent.txt",
            "chunk_count": 2,
        }]

        db_path = str(tmp / "meta.db")
        from backend.services.page_store import _ensure_page_schema
        _ensure_page_schema(db_path)
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO document_pages VALUES (?, ?, ?, ?, ?, ?)",
            ("doc-1", "text-v0", 1, "OLD", "paragraph", "2026-01-01"),
        )
        conn.commit()
        conn.close()

        with pytest.raises(FileNotFoundError):
            rebuild.prepare_documents(
                records,
                tmp,
                parse_document=_fake_parse_single_page,
                chunk_document=_fake_chunk_fixed,
                chunk_size=5,
                overlap=1,
            )

        # 旧页仍存在
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM document_pages").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]["parser_version"] == "text-v0"

    def test_chunk_count_change_rejected(self, temp_dir):
        """chunk 数量变化时必须终止，不得绕过。"""
        from pathlib import Path
        tmp = Path(temp_dir)

        rebuild = _rebuild_module()
        source = tmp / "doc.txt"
        source.write_text("ABCDEFGHIJ", encoding="utf-8")  # 10 chars
        records = [{
            "document_id": "doc-1",
            "original_name": "test.txt",
            "stored_name": source.name,
            "chunk_count": 3,  # chunk_size=5, overlap=1 on 10 chars → 3 chunks
        }]

        prepared = rebuild.prepare_documents(
            records,
            tmp,
            parse_document=_fake_parse_single_page,
            chunk_document=_fake_chunk_fixed,
            chunk_size=5,
            overlap=1,
        )
        assert len(prepared[0].chunks) == 3

        records[0]["chunk_count"] = 5
        with pytest.raises(RuntimeError, match="doc-1.*登记为 5.*实际生成 3"):
            rebuild.prepare_documents(
                records,
                tmp,
                parse_document=_fake_parse_single_page,
                chunk_document=_fake_chunk_fixed,
                chunk_size=5,
                overlap=1,
            )
