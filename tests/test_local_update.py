"""Offline update transactions on small synthetic installs, no production data."""
import hashlib
import json
from pathlib import Path
import sqlite3
import zipfile

import pytest

from launcher import updater, portable, common
from deploy.windows import build_update


@pytest.fixture
def installation(tmp_path, monkeypatch):
    root = tmp_path / "中文 空格 安装目录"
    root.mkdir()
    files = {"runtime/python.exe": b"immutable runtime", "runtime/python314._pth": b".",
             "models/weights.bin": b"immutable model", "locks/w1-lock.json": b'{}',
             "backend/main.py": b"old backend", "launcher/portable.py": b"old launcher",
             "web/dist/index.html": b"old frontend", "web/dist/obsolete.js": b"old js",
             "使用说明.txt": b"old guide", "licenses/LICENSE.txt": b"preserve license"}
    files["release-manifest.json"] = build_update.encoded({"source_commit": "old-version",
        "frontend_package_lock_sha256": updater.digest(build_update.REPO / "web/package-lock.json")})
    for name in ("启动.cmd", "CPU启动.cmd", "停止.cmd", "维护.cmd"):
        files[name] = b"old entry"
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    manifest = {"format": 1, "stage": "W4 internal preview", "files": [build_update.entry(n, b) for n, b in files.items()],
                "frontend_build_id": hashlib.sha256(files["web/dist/index.html"]).hexdigest()}
    (root / "portable-manifest.json").write_bytes(build_update.encoded(manifest))
    (root / "data/uploads").mkdir(parents=True)
    (root / "data/uploads/原文.txt").write_text("保留的文档正文", encoding="utf-8")
    (root / "data/runtime_secrets.bin").write_bytes(b"encrypted secret sentinel")
    (root / "data/workspace.json").write_bytes(build_update.encoded({"contract": {"runtime_lock_sha256": updater.digest(root / "locks/w1-lock.json")}}))
    with sqlite3.connect(root / "data/metadata.db") as db:
        db.execute("CREATE TABLE schema_meta (schema_version INTEGER)")
        db.execute("INSERT INTO schema_meta VALUES (5)")
        db.execute("CREATE TABLE messages (content TEXT)")
        db.execute("INSERT INTO messages VALUES ('原会话内容')")
    # These functions are the device/process boundary; filesystem/SQLite are real.
    monkeypatch.setattr(portable, "stop", lambda *a, **kw: {"outcome": "stopped"})
    package = tmp_path / "本地更新.zip"
    build_update.build(root, package, notes="新增会话编辑与本地更新", allow_working_tree=True)
    return root, package, {name: updater.digest(root / name) for name in files}, (root / "data/metadata.db").read_bytes()


def queued(installation):
    root, package, _, _ = installation
    job = updater.stage(root, package)
    updater.queue(root, job["id"], launch=False)
    return job["id"]


def test_app_update_preserves_data_models_and_runtime_and_retires_old_assets(installation):
    root, package, old, database = installation
    job_id = queued(installation)
    result = updater.run_update(root, job_id, restart=False)
    assert result["phase"] == "completed", result
    assert updater.active(root) is None
    assert (root / "data/metadata.db").read_bytes() == database
    assert (root / "data/uploads/原文.txt").read_text("utf-8") == "保留的文档正文"
    assert (root / "data/runtime_secrets.bin").read_bytes() == b"encrypted secret sentinel"
    for name in ("runtime/python.exe", "models/weights.bin", "licenses/LICENSE.txt"):
        assert updater.digest(root / name) == old[name]
    assert not (root / "web/dist/obsolete.js").exists()
    assert (updater.job_path(root, job_id) / "retired/web/dist/obsolete.js").exists()
    common.validate_assets(root, full=True)


def test_failed_candidate_after_database_migration_restores_exact_old_data_and_program(installation, monkeypatch):
    root, _, old, database = installation
    job_id = queued(installation)
    def failed_start(*args, **kwargs):
        db = sqlite3.connect(root / "data/metadata.db")
        try:
            with db:
                db.execute("UPDATE schema_meta SET schema_version=6")
                db.execute("UPDATE messages SET content='candidate changes'")
        finally:
            db.close()
        (root / "data/uploads/new.txt").write_bytes(b"new candidate data")
        raise RuntimeError("模拟新版启动失败")
    monkeypatch.setattr(portable, "start", failed_start)
    state = updater.run_update(root, job_id)
    assert state["phase"] == "rolled_back", state
    assert "模拟新版启动失败" in state["error"]
    assert (root / "data/metadata.db").read_bytes() == database
    assert not (root / "data/uploads/new.txt").exists()
    assert list(updater.job_path(root, job_id).glob("failed-data-*"))
    for name, digest in old.items():
        assert updater.digest(root / name) == digest
    assert not (root / "更新.cmd").exists()


def test_interrupted_replacement_blocks_normal_start_and_can_recover(installation, monkeypatch):
    root, _, old, database = installation
    job_id = queued(installation)
    original = updater._atomic_copy
    def interrupt(source, target):
        if target == root / "backend/main.py":
            raise KeyboardInterrupt()
        return original(source, target)
    monkeypatch.setattr(updater, "_atomic_copy", interrupt)
    with pytest.raises(KeyboardInterrupt):
        updater.run_update(root, job_id, restart=False)
    with pytest.raises(RuntimeError, match="未完成"):
        updater.guard_start(root)
    monkeypatch.setattr(updater, "_atomic_copy", original)
    state = updater.run_update(root, job_id, restart=False)
    assert state["phase"] == "rolled_back"
    assert (root / "data/metadata.db").read_bytes() == database
    for name, digest in old.items():
        assert updater.digest(root / name) == digest


def test_staged_package_change_is_rejected_before_queue(installation):
    root, _, _, database = installation
    job = updater.stage(root, installation[1])
    with (updater.job_path(root, job["id"]) / "package.zip").open("ab") as stream:
        stream.write(b"tampered trailer")
    with pytest.raises(ValueError, match="已变化"):
        updater.queue(root, job["id"], launch=False)
    assert updater.active(root) is None
    assert (root / "data/metadata.db").read_bytes() == database


@pytest.mark.parametrize("name", ["../data/a", "backend/../../x", "backend/CON.py", "backend/a:b", "backend/foo.", "backend\\x.py"])
def test_invalid_paths_are_rejected(name):
    with pytest.raises(ValueError):
        updater.normalized(name)


@pytest.mark.parametrize("name", ["data/metadata.db", "runtime/python.exe", "models/weights.bin", ".env", "state/service.json"])
def test_package_cannot_declare_protected_files(installation, tmp_path, name):
    root, package, _, _ = installation
    with zipfile.ZipFile(package) as archive:
        blobs = {n: archive.read(n) for n in archive.namelist()}
    manifest = json.loads(blobs["update-manifest.json"])
    manifest["files"].append(build_update.entry(name, b"forbidden"))
    blobs["payload/" + name] = b"forbidden"
    blobs["update-manifest.json"] = build_update.encoded(manifest)
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w", zipfile.ZIP_DEFLATED) as archive:
        for n, content in blobs.items(): archive.writestr(n, content)
    with pytest.raises(ValueError, match="改写"):
        updater.inspect_package(root, bad)


def test_unsupported_base_is_rejected_after_update(installation):
    root, package, _, database = installation
    job_id = queued(installation)
    result = updater.run_update(root, job_id, restart=False)
    assert result["phase"] == "completed"
    with pytest.raises(ValueError, match="不支持"):
        updater.inspect_package(root, package)


def test_second_queue_is_blocked(installation):
    root, package, _, _ = installation
    first = updater.stage(root, package)
    second = updater.stage(root, package)
    updater.queue(root, first["id"], launch=False)
    with pytest.raises(RuntimeError, match="正在进行"):
        updater.queue(root, second["id"], launch=False)


def test_damaged_backup_requires_recovery_and_repaired_backup_can_resume(installation, monkeypatch):
    root, _, old, database = installation
    job_id = queued(installation)
    backup_file = updater.job_path(root, job_id) / "backup/data/uploads/原文.txt"
    def fail(*args, **kwargs):
        backup_file.write_bytes(b"damaged backup")
        raise RuntimeError("candidate failed")
    monkeypatch.setattr(portable, "start", fail)
    state = updater.run_update(root, job_id)
    assert state["phase"] == "recovery_required"
    assert (root / "data/metadata.db").read_bytes() == database
    with pytest.raises(RuntimeError, match="未完成"):
        updater.guard_start(root)
    backup_file.write_text("保留的文档正文", encoding="utf-8")
    assert updater.run_update(root, job_id)["phase"] == "rolled_back"
    for name, expected in old.items(): assert updater.digest(root / name) == expected


def test_dispatch_failure_clears_active_marker_without_replacing_files(installation, monkeypatch):
    root, package, old, _ = installation
    job = updater.stage(root, package)
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **kw: (_ for _ in ()).throw(OSError("启动失败")))
    with pytest.raises(OSError): updater.queue(root, job["id"])
    assert updater.active(root) is None
    assert updater.read_json(updater.job_path(root, job["id"]) / "journal.json")["phase"] == "rejected"
    for name, expected in old.items(): assert updater.digest(root / name) == expected


def test_pre_replacement_recovery_does_not_stop_existing_service(installation, monkeypatch):
    root, _, _, _ = installation
    job_id = queued(installation)
    monkeypatch.setattr(portable, "stop", lambda *a, **kw: pytest.fail("No files replaced; must not stop again"))
    updater.rollback(root, job_id)
    assert updater.active(root) is None


def test_same_target_version_is_rejected(installation, tmp_path):
    root, package, _, _ = installation
    job_id = queued(installation)
    assert updater.run_update(root, job_id, restart=False)["phase"] == "completed"
    with zipfile.ZipFile(package) as archive:
        blobs = {n: archive.read(n) for n in archive.namelist()}
    manifest = json.loads(blobs["update-manifest.json"])
    manifest["supported_base_assets"] = [common.load_assets(root)[1]]
    blobs["update-manifest.json"] = build_update.encoded(manifest)
    duplicate = tmp_path / "same-version.zip"
    with zipfile.ZipFile(duplicate, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in blobs.items(): archive.writestr(name, data)
    with pytest.raises(ValueError, match="无需重复"):
        updater.inspect_package(root, duplicate)


def test_interrupted_recovery_never_rewinds_already_restored_data(installation, monkeypatch):
    root, _, old, _ = installation
    job_id = queued(installation)
    def failed_start(*a, **kw):
        raise RuntimeError("模拟候选启动失败")
    monkeypatch.setattr(portable, "start", failed_start)
    original = updater._atomic_copy
    def interrupt_recovery(source, target):
        if 'backup' in source.parts and target == root / 'backend/main.py':
            raise KeyboardInterrupt()
        return original(source, target)
    monkeypatch.setattr(updater, '_atomic_copy', interrupt_recovery)
    with pytest.raises(KeyboardInterrupt): updater.run_update(root, job_id)
    assert updater.read_json(updater.job_path(root, job_id) / 'journal.json')['data_restore_complete']
    # Model a legacy app reopening after its files were recovered, before the
    # recovery record was finalized. A subsequent recovery must keep this write.
    db = sqlite3.connect(root / 'data/metadata.db')
    try:
        with db: db.execute("INSERT INTO messages VALUES ('恢复后新增的会话')")
    finally:
        db.close()
    monkeypatch.setattr(updater, '_atomic_copy', original)
    assert updater.run_update(root, job_id)['phase'] == 'rolled_back'
    db = sqlite3.connect(root / 'data/metadata.db')
    try:
        assert db.execute("SELECT content FROM messages WHERE content='恢复后新增的会话'").fetchone()
    finally:
        db.close()


def test_web_inspection_status_and_confirmation_use_same_engine(installation, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.routers import local_update
    root, package, _, _ = installation
    app = FastAPI()
    app.include_router(local_update.router, prefix="/api")
    monkeypatch.setattr(local_update, "installation", lambda: root)
    monkeypatch.setattr(local_update.settings, "DISTRIBUTION_MODE", "portable")
    original = updater.queue
    monkeypatch.setattr(updater, "queue", lambda root, job: original(root, job, launch=False))
    with TestClient(app) as client:
        assert client.get("/api/local-update/status").json()["available"]
        response = client.post("/api/local-update/inspect", files={"file": ("update.zip", package.read_bytes(), "application/zip")})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["phase"] == "staged"
        assert updater.active(root) is None
        confirmed = client.post(f"/api/local-update/{result['id']}/apply")
        assert confirmed.status_code == 200 and confirmed.json()["phase"] == "queued"
        assert client.get("/api/local-update/status").json()["job"]["id"] == result["id"]
        assert client.post("/api/local-update/invalid/apply").status_code == 409


def test_source_mode_cannot_upload_updates(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.routers import local_update
    monkeypatch.setattr(local_update.settings, "DISTRIBUTION_MODE", "source")
    app = FastAPI()
    app.include_router(local_update.router, prefix="/api")
    with TestClient(app) as client:
        assert not client.get("/api/local-update/status").json()["available"]
        assert client.post("/api/local-update/inspect", files={"file": ("x.zip", b"x")}).status_code == 409


def test_candidate_maintenance_blocks_business_writes(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.services.update_maintenance import UpdateMaintenanceMiddleware
    app = FastAPI()
    @app.post('/api/chat')
    def chat(): return {"called": True}
    @app.get('/api/health')
    def health(): return {"status": "ok"}
    (tmp_path / "state").mkdir()
    marker = tmp_path / "state/update-active.json"
    marker.write_text('{}')
    app.add_middleware(UpdateMaintenanceMiddleware, root=tmp_path)
    with TestClient(app) as client:
        assert client.post('/api/chat').status_code == 503
        assert client.get('/api/health').status_code == 200
        marker.unlink()
        assert client.post('/api/chat').status_code == 200
