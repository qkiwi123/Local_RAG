"""Synthetic fault injection OUTSIDE release payload; never run on personal data.

The worker pauses actual upload after Chroma writes, or index activation after a
real generation build. No test hooks are installed in application source/ZIP.
"""
from __future__ import annotations

import argparse
from contextlib import suppress
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from types import SimpleNamespace
import urllib.request
import uuid

sys.dont_write_bytecode = True


def wait_for(predicate, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.1)
    raise TimeoutError("Synthetic acceptance wait timed out")


def worker(args):
    root = args.root.resolve()
    os.environ.update(RAG_DISTRIBUTION_MODE="portable", RAG_PORTABLE_DEVICE="cuda:0")
    from backend.portable import prepare_environment
    prepare_environment(root, "cuda:0")
    if args.phase == "inspect":
        from backend.services.vector_store import _collection
        rows = _collection.get(where={"document_id": args.document_id})
        print(json.dumps({"remaining_chunks": len(rows["ids"])}), flush=True)
        return
    marker = root / f"temp/w4-{args.phase}.json"
    release = root / f"temp/w4-{args.phase}.release"
    def pause(payload):
        marker.write_text(json.dumps(payload), encoding="utf-8")
        wait_for(release.exists, timeout=240)
    if args.phase in {"upload", "graceful"}:
        from backend.routers import upload
        original = upload.add_chunks
        def add_chunks(chunks):
            count = original(chunks)
            pause({"document_id": chunks[0].metadata.document_id, "chunk_count": count})
            return count
        upload.add_chunks = add_chunks
    else:
        from backend.services import bm25s_store
        original = bm25s_store.activate_generation
        def activate(root, generation_id, **kwargs):
            if (marker.parent / "w4-rebuild.armed").exists():
                pause({"generation_id": generation_id})
            return original(root, generation_id, **kwargs)
        bm25s_store.activate_generation = activate
    from launcher.common import load_assets
    from launcher.portable import serve
    _, asset_id = load_assets(root)
    serve(root, SimpleNamespace(instance_id=args.instance_id, asset_id=asset_id,
                               port=args.port, device="cuda:0", batch_profile="normal"))


def exercise(root):
    from launcher.common import get_health, read_record, same_process
    def cli(*args, timeout=240):
        result = subprocess.run([str(root / "runtime/python.exe"), str(root / "launcher/portable.py"), *args],
                                cwd=root, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
        if result.returncode:
            raise AssertionError(result.stderr.decode("utf-8", errors="replace")[-1800:])
        return next(json.loads(s) for s in reversed(result.stdout.decode("utf-8").splitlines()) if s.startswith("{"))
    def request(path, *, body=None, content_type="application/json"):
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open(urllib.request.Request(f"http://127.0.0.1:{port}" + path, data=body,
                                            headers={"Content-Type": content_type}), timeout=180) as response:
            return json.load(response)
    def upload(label):
        boundary = uuid.uuid4().hex
        text = f"{label}：备份知识库前停止后台，再复制完整目录。上传文件、向量、元数据、索引随目录保留。"
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{label}.txt"\r\n'
                f'Content-Type: text/plain\r\n\r\n{text}\r\n--{boundary}--\r\n').encode()
        return request("/api/upload", body=body, content_type="multipart/form-data; boundary=" + boundary)
    def ready():
        health = get_health(port)
        return health and health["model_warmup"]["status"] == "ready"
    def sparse_ready():
        health = get_health(port)
        return health and health["retrieval"]["sparse_index_status"] == "ready"
    def begin_upload(label):
        result = []
        def target():
            try:
                result.append(upload(label))
            except Exception as error:
                result.append(type(error).__name__)
        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        return thread, result
    def launch_worker(phase):
        command = [str(root / "runtime/python.exe"), str(Path(__file__).resolve()), "worker",
                   "--root", str(root), "--phase", phase, "--port", str(port),
                   "--instance-id", uuid.uuid4().hex, "--launcher-script", str(root / "launcher/portable.py"),
                   "--action", "serve"]
        with (root / "logs/w4-worker.log").open("ab") as log:
            child = subprocess.Popen(command, cwd=root, stdout=log, stderr=log)
        owned.append(child)
        wait_for(ready)
        assert same_process(root, read_record(root))
        if phase == "rebuild":
            wait_for(sparse_ready)
            (root / "temp/w4-rebuild.armed").write_text("armed")
        return child
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    checks = {}
    owned = []
    try:
        print("W4: start extracted ZIP and create baseline via actual GPU HTTP upload", flush=True)
        cli("start", "--no-browser", "--port", str(port))
        baseline = upload("baseline")
        wait_for(sparse_ready)
        cli("stop")
        print("W4: force stop after actual Chroma upload write; restart recovery", flush=True)
        child = launch_worker("upload")
        thread, result = begin_upload("interrupted")
        marker = root / "temp/w4-upload.json"
        wait_for(marker.exists)
        interrupted = json.loads(marker.read_text("utf-8"))["document_id"]
        assert json.loads(marker.read_text("utf-8"))["chunk_count"] > 0
        cli("stop", "--timeout", "0", "--force")
        child.wait(timeout=20)
        thread.join(timeout=20)
        assert not thread.is_alive()
        cli("start", "--no-browser", "--port", str(port))
        wait_for(sparse_ready)
        with sqlite3.connect(root / "data/metadata.db") as db:
            assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert db.execute("SELECT status FROM documents WHERE document_id=?", (interrupted,)).fetchone() == ("failed",)
            assert db.execute("SELECT status FROM documents WHERE document_id=?", (baseline["document_id"],)).fetchone() == ("ready",)
        assert (root / f"data/uploads/.{interrupted}.uploading.txt").is_file()
        cli("stop")
        inspected = subprocess.run([str(root / "runtime/python.exe"), str(Path(__file__).resolve()), "worker", "--root", str(root),
                                    "--phase", "inspect", "--document-id", interrupted], cwd=root, capture_output=True, timeout=60, check=True)
        view = next(json.loads(s) for s in reversed(inspected.stdout.decode("utf-8").splitlines()) if s.startswith("{"))
        assert view["remaining_chunks"] == 0
        checks["forced_upload_recovery_preserves_bytes_and_removes_partial_chunks"] = "passed"
        print("W4: force stop after actual Sparse generation build; restart publication", flush=True)
        child = launch_worker("rebuild")
        uploaded = upload("rebuild")
        wait_for((root / "temp/w4-rebuild.json").exists)
        cli("stop", "--timeout", "0", "--force")
        child.wait(timeout=20)
        cli("start", "--no-browser", "--port", str(port))
        wait_for(sparse_ready)
        assert request(f'/api/documents/id/{uploaded["document_id"]}/preview')
        answer = request("/api/chat", body=json.dumps({"question": "备份知识库前怎么操作？"}).encode())
        assert answer["citations"]
        cli("stop")
        checks["forced_sparse_build_restart_returns_ready_and_sources"] = "passed"
        print("W4: normal stop waits for paused actual upload then exits", flush=True)
        child = launch_worker("graceful")
        thread, result = begin_upload("graceful")
        wait_for((root / "temp/w4-graceful.json").exists)
        stopper = subprocess.Popen([str(root / "runtime/python.exe"), str(root / "launcher/portable.py"),
                                    "stop", "--timeout", "90"], cwd=root, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        owned.append(stopper)
        time.sleep(0.5)
        assert stopper.poll() is None and child.poll() is None
        (root / "temp/w4-graceful.release").write_text("release")
        thread.join(timeout=120)
        assert result and isinstance(result[0], dict), result
        out, err = stopper.communicate(timeout=120)
        assert stopper.returncode == 0, err[-1000:]
        child.wait(timeout=20)
        cli("start", "--no-browser", "--port", str(port))
        wait_for(sparse_ready)
        assert request(f'/api/documents/id/{result[0]["document_id"]}/preview')
        cli("stop")
        checks["normal_stop_drains_upload_then_restart_retains_document"] = "passed"
        with sqlite3.connect(root / "data/metadata.db") as db:
            assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        checks["sqlite_integrity_check"] = "passed"
        assert read_record(root) is None
        checks["all_test_services_stopped"] = True
        return checks
    finally:
        with suppress(Exception):
            cli("stop", "--timeout", "2", "--force", timeout=25)
        for child in owned:
            if child.poll() is None:
                child.kill()  # Only retained test-child handles.
                child.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["accept", "worker"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--phase", choices=["upload", "rebuild", "graceful", "inspect"])
    parser.add_argument("--port", type=int)
    parser.add_argument("--instance-id")
    parser.add_argument("--document-id")
    parser.add_argument("--launcher-script")
    parser.add_argument("--action")
    args = parser.parse_args()
    root = args.root.resolve()
    repository = Path(__file__).resolve().parents[2]
    if not root.is_relative_to(repository / "build/windows-release") or root == repository / "build/windows-release":
        parser.error("Only an isolated extracted W4 acceptance child is allowed")
    if args.mode == "worker":
        if Path(sys.executable).resolve() != root / "runtime/python.exe" or not sys.flags.isolated:
            parser.error("Fault worker requires this prototype embedded runtime")
        worker(args)
        return
    if (root / "data").exists():
        parser.error("Acceptance requires a NEW extracted package without data")
    sys.path.insert(0, str(root))
    (root / "reports").mkdir()
    report = {"status": "failed", "scope": "Extracted internal ZIP, synthetic controlled fault barriers, real local models and HTTP; no remote API"}
    try:
        report["checks"] = exercise(root)
        report["status"] = "passed"
    except Exception as error:
        report["error"] = type(error).__name__ + ": " + str(error)
        traceback.print_exc()
    (root / "reports/w4-recovery.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)
    raise SystemExit(0 if report["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
