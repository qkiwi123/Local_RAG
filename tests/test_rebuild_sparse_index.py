"""P2 Sparse构建CLI的路径与显式参数安全测试。"""

from pathlib import Path
import subprocess
import sys

import pytest

from scripts import rebuild_sparse_index


def _sources(tmp_path: Path) -> tuple[Path, Path]:
    chroma = tmp_path / "source" / "chroma"
    chroma.mkdir(parents=True)
    metadata = tmp_path / "source" / "metadata.db"
    metadata.touch()
    return chroma, metadata


def _production_paths(tmp_path: Path) -> dict[str, Path]:
    """与源不重叠的假生产路径集合，避免测试读到真实.env。"""
    base = tmp_path / "production"
    return {
        "chroma": base / "chroma",
        "metadata": base / "metadata.db",
        "sparse_root": base / "sparse_index",
    }


def test_safe_paths_accept_separate_explicit_test_paths(tmp_path, monkeypatch):
    chroma, metadata = _sources(tmp_path)
    target = tmp_path / "output" / "sparse"
    monkeypatch.setattr(
        rebuild_sparse_index,
        "_configured_production_paths",
        lambda: _production_paths(tmp_path),
    )

    assert rebuild_sparse_index.ensure_safe_paths(
        source_chroma=chroma,
        source_metadata=metadata,
        target_root=target,
    ) == (chroma.resolve(), metadata.resolve(), target.resolve())


def test_safe_paths_reject_production_sources(tmp_path, monkeypatch):
    chroma, metadata = _sources(tmp_path)
    monkeypatch.setattr(
        rebuild_sparse_index,
        "_configured_production_paths",
        lambda: {
            "chroma": chroma.resolve(),
            "metadata": metadata.resolve(),
            "sparse_root": tmp_path / "production" / "sparse",
        },
    )
    with pytest.raises(ValueError, match="生产数据"):
        rebuild_sparse_index.ensure_safe_paths(
            source_chroma=chroma,
            source_metadata=metadata,
            target_root=tmp_path / "target",
        )


def test_safe_paths_reject_source_target_overlap(tmp_path, monkeypatch):
    chroma, metadata = _sources(tmp_path)
    monkeypatch.setattr(
        rebuild_sparse_index,
        "_configured_production_paths",
        lambda: _production_paths(tmp_path),
    )
    with pytest.raises(ValueError, match="互相包含"):
        rebuild_sparse_index.ensure_safe_paths(
            source_chroma=chroma,
            source_metadata=metadata,
            target_root=chroma / "sparse",
        )


def test_production_entry_requires_exact_path_match(tmp_path, monkeypatch):
    """受控生产入口：三条路径必须与生产配置精确匹配。"""
    chroma, metadata = _sources(tmp_path)
    production = _production_paths(tmp_path)
    monkeypatch.setattr(
        rebuild_sparse_index,
        "_configured_production_paths",
        lambda: production,
    )
    with pytest.raises(ValueError, match="精确匹配"):
        rebuild_sparse_index.ensure_safe_paths(
            source_chroma=chroma,
            source_metadata=metadata,
            target_root=production["sparse_root"],
            allow_production=True,
            production_confirm=str(production["chroma"]),
        )


def test_production_entry_requires_confirm_value(tmp_path, monkeypatch):
    chroma, metadata = _sources(tmp_path)
    production = _production_paths(tmp_path)
    production.update({"chroma": chroma.resolve(), "metadata": metadata.resolve()})
    monkeypatch.setattr(
        rebuild_sparse_index,
        "_configured_production_paths",
        lambda: production,
    )
    with pytest.raises(ValueError, match="production-confirm"):
        rebuild_sparse_index.ensure_safe_paths(
            source_chroma=chroma,
            source_metadata=metadata,
            target_root=production["sparse_root"],
            allow_production=True,
            production_confirm="wrong-confirm",
        )


def test_production_entry_accepts_exact_match_and_confirm(tmp_path, monkeypatch):
    chroma, metadata = _sources(tmp_path)
    production = _production_paths(tmp_path)
    production.update({"chroma": chroma.resolve(), "metadata": metadata.resolve()})
    monkeypatch.setattr(
        rebuild_sparse_index,
        "_configured_production_paths",
        lambda: production,
    )

    assert rebuild_sparse_index.ensure_safe_paths(
        source_chroma=chroma,
        source_metadata=metadata,
        target_root=production["sparse_root"],
        allow_production=True,
        production_confirm=str(production["chroma"]),
    ) == (chroma.resolve(), metadata.resolve(), production["sparse_root"].resolve())


def test_cli_requires_all_source_and_contract_arguments():
    parser = rebuild_sparse_index._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_cli_file_entrypoint_can_import_project_modules():
    result = subprocess.run(
        [sys.executable, str(Path(rebuild_sparse_index.__file__).resolve()), "--help"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--source-chroma" in result.stdout


def test_default_entry_rejects_external_production_sparse_target(tmp_path, monkeypatch):
    chroma, metadata = _sources(tmp_path)
    production = _production_paths(tmp_path)
    monkeypatch.setattr(rebuild_sparse_index, "_configured_production_paths", lambda: production)
    for target in (production["sparse_root"], production["sparse_root"] / "nested"):
        with pytest.raises(ValueError, match="生产目录"):
            rebuild_sparse_index.ensure_safe_paths(
                source_chroma=chroma, source_metadata=metadata, target_root=target
            )


def test_source_registration_requires_exact_ids_and_per_document_counts():
    chunks = [{"metadata": {"document_id": "a"}}, {"metadata": {"document_id": "b"}}]
    for expected in ({"x": 1, "y": 1}, {"a": 2, "b": 0}):
        with pytest.raises(RuntimeError, match="逐文档"):
            rebuild_sparse_index._verify_registered_chunks(chunks, {"document_chunk_counts": expected})
    rebuild_sparse_index._verify_registered_chunks(chunks, {"document_chunk_counts": {"a": 1, "b": 1}})


def test_source_fingerprint_includes_sqlite_wal(tmp_path):
    db = tmp_path / "metadata.db"
    db.write_bytes(b"database")
    before = rebuild_sparse_index._metadata_fingerprint(db)
    Path(str(db) + "-wal").write_bytes(b"new committed rows")
    after = rebuild_sparse_index._metadata_fingerprint(db)
    assert before[""] == after[""]
    assert before != after


def test_production_paths_use_environment_override(tmp_path, monkeypatch):
    monkeypatch.setenv("SPARSE_INDEX_ROOT", str(tmp_path / "override"))
    assert rebuild_sparse_index._configured_production_paths()["sparse_root"] == tmp_path / "override"


@pytest.mark.parametrize("change_source", [False, True])
def test_controlled_build_on_isolated_real_storage(tmp_path, monkeypatch, change_source):
    """真实SQLite/Chroma副本验证受控入口，所有生产路径均指向tmp_path。"""
    import json
    import sqlite3
    import chromadb
    from backend.services import bm25s_store

    chroma = tmp_path / "source" / "chroma"
    chroma.mkdir(parents=True)
    metadata = chroma.parent / "metadata.db"
    with sqlite3.connect(metadata) as connection:
        connection.execute("CREATE TABLE documents (document_id TEXT, original_name TEXT, stored_name TEXT, status TEXT, chunk_count INTEGER)")
        connection.executemany("INSERT INTO documents VALUES (?, ?, ?, 'ready', 1)",
                               [(str(i), f"{i}.txt", f"{i}.txt") for i in range(3)])
    client = chromadb.PersistentClient(path=str(chroma))
    collection = client.create_collection("knowledge_base")
    collection.add(ids=["0", "1", "2"], documents=["二叉树前序遍历", "进程调度算法", "数据库索引"],
                   metadatas=[{"document_id": str(i)} for i in range(3)], embeddings=[[1., 0.]] * 3)
    client._system.stop()
    target = tmp_path / "output"
    monkeypatch.setattr(rebuild_sparse_index, "_configured_production_paths",
                        lambda: {"chroma": chroma, "metadata": metadata, "sparse_root": target})
    # audit hook是进程永久状态；这里以测试替身隔离，CLI本身保留禁止出网逻辑。
    monkeypatch.setattr(rebuild_sparse_index, "_install_network_guard", lambda *args: None)
    for key in ("ANONYMIZED_TELEMETRY", "DO_NOT_TRACK", "PYTHONDONTWRITEBYTECODE",
                "PREWARM_MODELS", "CHROMA_PERSIST_DIR", "METADATA_DB_PATH", "UPLOAD_DIR"):
        monkeypatch.setenv(key, __import__("os").environ.get(key, ""))
    real_build = bm25s_store.build_generation

    def build_then_change(**kwargs):
        result = real_build(**kwargs)
        if change_source:
            with sqlite3.connect(metadata) as connection:
                connection.execute("UPDATE documents SET original_name = 'changed.txt' WHERE document_id = '0'")
        return result

    monkeypatch.setattr(bm25s_store, "build_generation", build_then_change)
    report = tmp_path / "report.json"
    argv = ["--source-chroma", str(chroma), "--source-metadata", str(metadata),
            "--target-root", str(target), "--corpus-id", "production-isolated-test",
            "--oracle-questions", "eval/crud_rag_1000/v3_1/questions_v3_120q.json",
            "--report", str(report), "--parser-version", "test-parser",
            "--chunk-strategy", "fixed", "--chunk-size", "500", "--chunk-overlap", "50",
            "--chunk-max-tokens", "480", "--generation-id", "controlled-test",
            "--allow-production", "--production-confirm", str(chroma)]
    if change_source:
        with pytest.raises(RuntimeError, match="发生变化"):
            rebuild_sparse_index.main([*argv, "--publish"])
        assert not report.exists()
    else:
        assert rebuild_sparse_index.main(argv) == 0
        result = json.loads(report.read_text(encoding="utf-8"))
        assert result["source_unchanged"]
        assert result["reload_oracle"]["passed"]
        assert result["reload_oracle"]["question_count"] == 120
        assert not result["published"]
        with pytest.raises(FileExistsError, match="报告已存在"):
            rebuild_sparse_index.main(argv)
    assert not (target / "active.json").exists()
