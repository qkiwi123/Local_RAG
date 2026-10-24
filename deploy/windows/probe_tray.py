"""Real local CPU/GPU, automatic port and native tray lifecycle acceptance.

Only a fresh build/tray-dev/RAG synthetic tree. Desktop menu clicks are a
separate manual/Computer Use check; this probe invokes the same bound action.
"""
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import uuid

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "build/tray-dev/RAG"
sys.path.insert(0, str(ROOT))
from launcher.common import get_health, read_record, read_json, same_process
from launcher.tray import Controller


def wait(predicate, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if value := predicate(): return value
        time.sleep(0.2)
    raise TimeoutError("Acceptance deadline")


def cli(args, success=True):
    p = subprocess.run([str(ROOT / "runtime/python.exe"), str(ROOT / "launcher/portable.py"), *args],
                       cwd=ROOT / "temp", stdin=subprocess.DEVNULL, capture_output=True, timeout=360)
    if (p.returncode == 0) != success:
        raise AssertionError(p.stderr.decode("utf-8", errors="replace")[-2000:])
    if not success: return
    return next(json.loads(line) for line in reversed(p.stdout.decode("utf-8").splitlines()) if line.startswith("{"))


def request(port, path, body=None, content_type="application/json"):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request(f"http://127.0.0.1:{port}" + path, data=body,
                                            headers={"Content-Type": content_type}), timeout=90) as response:
        return json.load(response)


def main():
    if not ROOT.is_dir() or (ROOT / "data").exists():
        raise ValueError("Only a new isolated synthetic prototype is allowed")
    class Foreign(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"foreign":true}')
        def log_message(self, *a): pass
    dummy = None
    try:
        dummy = HTTPServer(("127.0.0.1", 8000), Foreign)
    except OSError:
        pass  # Preserve the user's existing listener, never query or stop it.
    if dummy:
        threading.Thread(target=dummy.serve_forever, daemon=True).start()
    record = None
    result = {"status": "failed", "scope": "Real synthetic CPU/GPU, native tray creation, action API and auto port; no remote model API"}
    try:
        print("Tray acceptance: explicit occupied port must fail; auto CPU starts...", flush=True)
        cli(["start", "--cpu", "--port", "8000", "--no-browser", "--no-tray"], success=False)
        record = cli(["start", "--cpu"])
        assert record["port"] != 8000 and record["device"] == "cpu"
        assert record["tray"]["outcome"] == "started", (ROOT / "logs/tray.log").read_text("utf-8", errors="replace")
        saved_tray = read_json(ROOT / "state/tray.json")
        assert same_process(ROOT, saved_tray, action="tray") and saved_tray["icon_ready"]
        health = get_health(record["port"])
        assert health["runtime"]["port"] == record["port"]
        again = cli(["start", "--cpu", "--no-browser"])
        assert again["outcome"] == "reused" and again["tray"]["pid"] == saved_tray["pid"]
        boundary = uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="tray-test.txt"\r\n'
                'Content-Type: text/plain\r\n\r\n知识库备份前请停止本目录后台，然后复制整个目录。\r\n'
                f'--{boundary}--\r\n').encode()
        uploaded = request(record["port"], "/api/upload", body, "multipart/form-data; boundary=" + boundary)
        wait(lambda: get_health(record["port"])["retrieval"]["sparse_index_status"] == "ready")
        answer = request(record["port"], "/api/chat", json.dumps({"question": "知识库怎么备份？"}).encode())
        assert answer["citations"]
        live = {"port": record["port"], "service_pid": record["pid"], "tray_pid": saved_tray["pid"], "hwnd": saved_tray["hwnd"]}
        (ROOT / "reports/tray-live.json").write_text(json.dumps(live, indent=2), encoding="utf-8")
        print("Tray live for desktop observation:", json.dumps(live), flush=True)
        wait(lambda: (ROOT / "temp/finish-desktop-check").exists(), seconds=600)
        assert Controller(ROOT, record).request_stop()
        wait(lambda: not same_process(ROOT, record))
        wait(lambda: not (ROOT / "state/tray.json").exists(), seconds=15)
        assert not same_process(ROOT, saved_tray, action="tray")
        print("CPU tray action stopped backend and icon; testing GPU auto restart...", flush=True)
        record = cli(["start", "--no-browser", "--no-tray"])
        assert record["device"] == "cuda:0" and record["port"] != 8000
        answer = request(record["port"], "/api/chat", json.dumps({"question": "知识库怎么备份？"}).encode())
        assert answer["citations"]
        Controller(ROOT, record).request_stop()
        wait(lambda: not same_process(ROOT, record))
        result.update(status="passed", checks={"explicit_occupied_port_refused":True,"auto_cpu_and_gpu_ports":True,
            "health_actual_port":True,"native_tray_ready":True,"repeat_start_single_tray":True,
            "real_cpu_gpu_hybrid_sources":True,"bound_tray_stop_action":True,"tray_exits_with_backend":True,
            "data_retained_across_device_restart":True,"all_test_services_stopped":True})
        if dummy: assert request(8000, "/")["foreign"]
    except Exception as error:
        result["error"] = str(error)
        traceback.print_exc()
    finally:
        if record:
            with suppress(Exception): Controller(ROOT, record).force_stop()
        if dummy:
            dummy.shutdown(); dummy.server_close()
        (ROOT / "reports/tray-acceptance.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result), flush=True)
    raise SystemExit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__": main()
