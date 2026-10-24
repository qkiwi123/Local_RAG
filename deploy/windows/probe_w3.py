"""Real isolated W3 command/HTTP lifecycle acceptance; synthetic data, no API key."""
from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid

sys.dont_write_bytecode = True


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def run(root: Path, args: list[str], *, wrapper: str | None = None, expected: int = 0, timeout: float = 300) -> dict:
    command = (["cmd.exe", "/d", "/c", str(root / wrapper), *args] if wrapper else
               [str(root / "runtime/python.exe"), str(root / "launcher/portable.py"), *args])
    result = subprocess.run(command, cwd=root / "temp", stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    output = result.stdout.decode("utf-8", errors="replace")
    errors = result.stderr.decode("utf-8", errors="replace")
    if (result.returncode == 0) != (expected == 0):
        raise AssertionError(f"CLI failed: {command[-6:]}\n{output[-3000:]}\n{errors[-3000:]}")
    for line in reversed(output.splitlines()):
        if line.strip().startswith("{"):
            return json.loads(line)
    return {"returncode": result.returncode, "tail": errors[-1000:]}


def request(port: int, path: str, *, method: str = "GET", body: bytes | None = None,
            headers: dict | None = None) -> tuple[int, bytes, dict]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    message = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, method=method, headers=headers or {})
    try:
        response = opener.open(message, timeout=60)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.read(), dict(response.headers)


def exercise(root: Path) -> dict:
    import psutil
    from launcher.common import get_health, matches_health, read_record, root_id, same_process, write_json

    port = free_port()
    command = [str(root / "runtime/python.exe"), str(root / "launcher/portable.py"),
               "start", "--no-browser", "--port", str(port)]
    print("W3: concurrent GPU start via CMD and direct launcher...", flush=True)
    commands = [["cmd.exe", "/d", "/c", str(root / "启动.cmd"), "--no-browser", "--port", str(port)], command]
    children = [subprocess.Popen(item, cwd=root / "temp", stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE) for item in commands]
    outcomes = []
    for child in children:
        output, errors = child.communicate(timeout=300)
        assert child.returncode == 0, (output.decode("utf-8", errors="replace"), errors.decode("utf-8", errors="replace"))
        outcomes.append(next(json.loads(line) for line in reversed(output.decode("utf-8").splitlines()) if line.startswith("{")))
    assert sorted(item["outcome"] for item in outcomes) == ["reused", "started"]
    assert len({item["instance_id"] for item in outcomes}) == 1
    record = read_record(root)
    assert same_process(root, record) and matches_health(root, record, get_health(port))
    assert record["device"] == "cuda:0", "This GPU-host case must not silently become CPU"
    result = {"concurrent_start": "passed", "gpu_device": record["device"], "port": port}
    status, page, _ = request(port, "/settings")
    assert status == 200 and hashlib.sha256(page).hexdigest() == record["frontend_build_id"]
    script = re.search(rb'src="(/assets/[^\"]+\.js)"', page).group(1).decode()
    status, _, headers = request(port, script)
    assert status == 200 and "javascript" in headers.get("content-type", headers.get("Content-Type", ""))
    result["react_settings_and_js_mime"] = "passed"
    status, settings_body, _ = request(port, "/api/settings")
    settings_view = json.loads(settings_body)
    assert status == 200 and not settings_view["llm"]["enabled"] and not settings_view["vision"]["enabled"]
    assert not settings_view["llm"]["api_key"]["configured"]
    result["generation_vision_and_inherited_key_disabled"] = "passed"
    for method, path, extra in [
        ("GET", "/api/health", {"Host": f"evil.example:{port}"}),
        ("POST", "/api/conversations", {"Origin": "http://evil.example", "Content-Type": "text/plain"}),
        ("POST", "/api/conversations", {"Origin": "null", "Content-Type": "application/json"}),
        ("POST", "/api/conversations", {"Origin": f"http://127.0.0.1:{port + 1}", "Content-Type": "application/json"}),
    ]:
        assert request(port, path, method=method, body=b'{}' if method == "POST" else None, headers=extra)[0] == 403
    result["host_origin_simple_post"] = "passed"
    boundary = "w3" + uuid.uuid4().hex
    text = "知识库备份：停止服务后复制整个目录，包含原文、元数据、向量库和Sparse索引。模型和运行环境随包保留。"
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="w3-backup.txt"\r\n'
            f'Content-Type: text/plain\r\n\r\n{text}\r\n--{boundary}--\r\n').encode("utf-8")
    status, data, _ = request(port, "/api/upload", method="POST", body=body,
                              headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    assert status == 200, data
    document_id = json.loads(data)["document_id"]
    deadline = time.monotonic() + 60
    while get_health(port)["retrieval"]["sparse_index_status"] != "ready":
        assert time.monotonic() < deadline
        time.sleep(0.2)
    status, data, _ = request(port, "/api/chat", method="POST", body=json.dumps({"question": "知识库怎么备份？"}).encode(),
                              headers={"Content-Type": "application/json"})
    assert status == 200 and json.loads(data)["citations"], data
    result["real_http_upload_hybrid_sources"] = "passed"
    print("W3: GPU HTTP/React/Host-Origin passed; stopping and checking foreign listener...", flush=True)
    assert run(root, ["--timeout", "30"], wrapper="停止.cmd")["outcome"] == "stopped"
    assert not same_process(root, record)
    # Controlled foreign server: always HTTP 200, still must never be adopted.
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        def log_message(self, *args):
            pass
    dummy = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=dummy.serve_forever, daemon=True)
    thread.start()
    try:
        other_port = dummy.server_port
        assert request(other_port, "/api/health")[0] == 200
        run(root, ["start", "--cpu", "--no-browser", "--port", str(other_port)], expected=1)
        assert request(other_port, "/api/health")[0] == 200
        result["foreign_http200_port_untouched"] = "passed"
    finally:
        dummy.shutdown()
        dummy.server_close()
        thread.join(timeout=5)
    # A stale PID record deliberately points at this test process, not a user process.
    stale = {**record, "pid": os.getpid(), "created_at": psutil.Process().create_time() + 3600,
             "instance_id": uuid.uuid4().hex, "root_id": root_id(root)}
    write_json(root, "state/service.json", stale)
    assert run(root, ["stop", "--force", "--timeout", "0"])["outcome"] == "not_running"
    result["stale_pid_not_killed"] = "passed"
    # Stop while the new server is still initializing, before browser readiness.
    pending = subprocess.Popen([str(root / "runtime/python.exe"), str(root / "launcher/portable.py"),
                                "start", "--cpu", "--no-browser", "--port", str(port)],
                                cwd=root / "temp", stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.monotonic() + 120
    while True:
        current = read_record(root)
        if current and current["instance_id"] != stale["instance_id"] and same_process(root, current):
            break
        assert pending.poll() is None and time.monotonic() < deadline
        time.sleep(0.05)
    assert run(root, ["stop", "--timeout", "45"], timeout=60)["outcome"] == "stopped"
    _, errors = pending.communicate(timeout=60)
    assert pending.returncode != 0, errors.decode("utf-8", errors="replace")
    assert read_record(root) is None
    result["stop_during_initialization"] = "passed"
    print("W3: initialization cancelled cleanly; testing CPU CMD and forced owned stop...", flush=True)
    # CPU CMD forwards its options and works independently of caller cwd.
    cpu = run(root, ["--no-browser", "--port", str(port)], wrapper="CPU启动.cmd")
    assert cpu["device"] == "cpu"
    status, data, _ = request(port, f"/api/documents/id/{document_id}/preview")
    assert status == 200, data
    assert get_health(port)["model_warmup"]["status"] == "ready"
    result["cpu_cmd_restart_retains_document"] = "passed"
    # Explicit --force, idle synthetic instance only; exercise exact-process checks.
    assert run(root, ["stop", "--timeout", "0", "--force"])["outcome"] == "stopped"
    assert not same_process(root, cpu)
    result["explicit_force_stop_owned_instance"] = "passed"
    assert run(root, ["--no-browser", "--port", str(port)], wrapper="CPU启动.cmd")["device"] == "cpu"
    (root / "temp/unknown-user-file.txt").write_text("keep", encoding="utf-8")
    run(root, ["maintain", "--action", "cache", "--yes"], expected=1)
    assert (root / "temp/unknown-user-file.txt").is_file()
    removed = run(root, ["--action", "prepare-remove", "--yes"], wrapper="维护.cmd")
    assert removed["outcome"] == "ready_for_manual_removal"
    assert (root / "data/metadata.db").is_file() and (root / "models/embedding/pytorch_model.bin").is_file()
    cleaned = run(root, ["maintain", "--action", "cache", "--yes"])
    assert cleaned["outcome"] == "cache_cleared" and (root / "temp/unknown-user-file.txt").is_file()
    assert not (root / "state/service.json").exists()
    result["maintenance_stops_preserves_data_models_unknown"] = "passed"
    result["cache_files_removed"] = cleaned["files"]
    result["all_test_services_stopped"] = True
    return result


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    root = Path(__file__).resolve().parents[1]
    if not sys.flags.isolated or Path(sys.executable).resolve() != (root / "runtime/python.exe").resolve():
        raise ValueError("Use the W3 prototype embedded interpreter")
    if (root / "data").exists():
        raise ValueError("Only a NEW W3 synthetic validation tree is allowed")
    os.environ.update(PYTHONUTF8="1", DEEPSEEK_API_KEY="w3-poison-not-real", LLM_ENABLED="true",
                      UPLOAD_DIR="C:\\rag-w3-must-not-create")
    from launcher.common import safe_path
    report = {"status": "failed", "scope": "W3 isolated CMD/process/real HTTP/real models; no remote API"}
    try:
        report["checks"] = exercise(root)
        report["status"] = "passed"
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        traceback.print_exc()
    finally:
        # Only this prototype's verified instance, never source/production processes.
        with __import__("contextlib").suppress(Exception):
            run(root, ["stop", "--force", "--timeout", "5"], timeout=30)
    safe_path(root, "reports/w3-lifecycle.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
