"""Release exclusion/repeatability and interrupted-ingest/shutdown safety."""
import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import zipfile

import pytest

from deploy.windows import build_release as build


def test_public_release_gate_rejects_before_git_or_writes(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["build_release.py"])
    with pytest.raises(SystemExit) as error:
        build.main()
    assert error.value.code == 2
    assert "Public release blocked" in capsys.readouterr().err


def test_deep_license_is_relocated_but_executable_path_is_not():
    long_license = "runtime/Lib/site-packages/torch.dist-info/licenses/" + "third_party/" * 13 + "LICENSE.txt"
    assert build.package_path(long_license, 42) == "licenses/relocated/00042-LICENSE.txt"
    assert build.package_path("runtime/Lib/site-packages/torch/__init__.py", 42).endswith("torch/__init__.py")
    with pytest.raises(ValueError, match="layout budget"):
        build.package_path("runtime/" + "module/" * 30 + "__init__.py", 42)


@pytest.fixture
def payload(tmp_path):
    stage = tmp_path / "LocalRAG-preview"
    stage.mkdir()
    (stage / "使用说明.txt").write_text("中文 空格", encoding="utf-8")
    item = stage / "使用说明.txt"
    build.dump(stage / "portable-manifest.json", {"files": [{"path": item.name, "size": item.stat().st_size,
                                                              "sha256": build.digest(item)}]})
    return stage


@pytest.mark.parametrize("relative", ["data/metadata.db", "reports/a.json", "state/service.json", ".env", "backend/.env.local", "validation/probe.py"])
def test_release_rejects_private_and_runtime_files(payload, relative):
    p = payload / relative
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("private", encoding="utf-8")
    manifest = json.loads((payload / "portable-manifest.json").read_text("utf-8"))
    manifest["files"].append({"path": relative})
    build.dump(payload / "portable-manifest.json", manifest)
    with pytest.raises(ValueError, match="Private/runtime"):
        build.assert_clean_payload(payload)


def test_zip_reproducible_crc_utf8_and_no_overwrite(payload, tmp_path):
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    a = build.export_zip(payload, first)
    b = build.export_zip(payload, second)
    assert a["zip_sha256"] == b["zip_sha256"]
    with zipfile.ZipFile(first) as z:
        assert z.testzip() is None
        assert z.read("LocalRAG-preview/使用说明.txt").decode() == "中文 空格"
    original = first.read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        build.export_zip(payload, first)
    assert first.read_bytes() == original


def test_unknown_release_file_rejected(payload):
    (payload / "unexpected.txt").write_text("unexpected")
    with pytest.raises(ValueError, match="Unexpected"):
        build.assert_clean_payload(payload)


def test_tampered_release_file_rejected_before_export(payload, tmp_path):
    (payload / "使用说明.txt").write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed after manifest"):
        build.export_zip(payload, tmp_path / "tampered.zip")
    assert not (tmp_path / "tampered.zip").exists()


def test_stage_cannot_reuse_or_escape(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "REPO", tmp_path)
    with pytest.raises(ValueError):
        build.new_stage(tmp_path / "outside")
    stage = build.new_stage(tmp_path / "build/windows-release/fresh")
    with pytest.raises(ValueError):
        build.new_stage(stage)


@pytest.mark.asyncio
async def test_shutdown_error_does_not_skip_remaining_resources(monkeypatch, caplog):
    from backend.services import portable_shutdown, sparse_rebuild, sparse_retriever
    events = []
    def broken(**_):
        raise RuntimeError("injected worker failure")
    monkeypatch.setattr(sparse_rebuild, "stop_worker", broken)
    monkeypatch.setattr(sparse_retriever, "clear_cache", lambda: events.append("cache"))
    states = iter([True, False])
    thread = SimpleNamespace(is_alive=lambda: next(states), join=lambda _: events.append("warmup"))
    result = await portable_shutdown.drain(thread)
    assert result == {"status": "failed", "failed_steps": ["sparse_worker"]}
    assert events == ["cache", "warmup"]
    assert "injected worker failure" in caplog.text


@pytest.mark.asyncio
async def test_shutdown_waits_for_worker_and_reports_success(monkeypatch):
    from backend.services import portable_shutdown, sparse_rebuild, sparse_retriever
    states = iter([False, True])
    monkeypatch.setattr(sparse_rebuild, "stop_worker", lambda **_: {"stopped": next(states)})
    monkeypatch.setattr(sparse_retriever, "clear_cache", lambda: None)
    assert (await portable_shutdown.drain())["status"] == "completed"


@pytest.mark.parametrize("fail_cleanup", [False, True])
def test_interrupted_ingest_cleanup_is_retryable_and_preserves_files(tmp_path, monkeypatch, fail_cleanup):
    from backend.config import settings
    from backend.services import portable_recovery, document_store, unit_store, vector_store
    db = tmp_path / "metadata.db"
    monkeypatch.setattr(settings, "METADATA_DB_PATH", str(db))
    document_store.create_document("interrupted", "draft.txt", "interrupted.txt", 4)
    document_store.create_document("complete", "ready.txt", "complete.txt", 4)
    document_store.mark_ready("complete", 1)
    original = tmp_path / ".interrupted.uploading.txt"
    original.write_bytes(b"keep")
    events = []
    def cleanup(identifier):
        events.append(identifier)
        if fail_cleanup:
            raise RuntimeError("injected vector failure")
    monkeypatch.setattr(vector_store, "delete_document_by_id", cleanup)
    monkeypatch.setattr(unit_store, "delete_document_units", lambda *a, **k: events.append("units"))
    if fail_cleanup:
        with pytest.raises(RuntimeError, match="injected"):
            portable_recovery.recover_interrupted_ingests()
        assert document_store.get_document("interrupted")["status"] == "processing"
    else:
        assert portable_recovery.recover_interrupted_ingests() == {"recovered": 1}
        assert document_store.get_document("interrupted")["status"] == "failed"
        assert portable_recovery.recover_interrupted_ingests() == {"recovered": 0}
    assert document_store.get_document("complete")["status"] == "ready"
    assert original.read_bytes() == b"keep"
    assert events[0] == "interrupted" and "complete" not in events
