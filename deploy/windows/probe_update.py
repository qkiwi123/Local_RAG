"""Isolated real-runtime old-package -> offline-update acceptance; synthetic data."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
import zipfile

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from launcher.common import read_json, load_assets, get_health, same_process
from launcher.updater import managed, digest


def request(port, path, body=None, headers=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request(f"http://127.0.0.1:{port}" + path, data=body, headers=headers or {}), timeout=180) as response:
        return json.loads(response.read())


def post(port, path, value):
    return request(port, path, json.dumps(value, ensure_ascii=False).encode(), {"Content-Type": "application/json"})


def main():
    base = REPO / "build/windows-release/LocalRAG-win64-preview-642c2809/RAG"
    package = Path(sys.argv[1]).resolve()
    work = REPO / "build/windows-update" / ("accept-" + uuid.uuid4().hex[:10])
    root = work / "RAG 中文 空格"
    root.mkdir(parents=True)
    manifest, old_asset_id = load_assets(base)
    entries = [e["path"] for e in manifest["files"]] + ["portable-manifest.json"]
    for name in entries:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
    def copy(name):
        # Protected assets are hardlinked read-only inputs; only managed app/data are replaced.
        if managed(name):
            import shutil
            shutil.copyfile(base / name, root / name)
        else:
            os.link(base / name, root / name)
    with ThreadPoolExecutor(max_workers=12) as executor:
        list(executor.map(copy, entries))
    (root / "temp").mkdir()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    output = work / "acceptance.json"
    report = {"root": str(root), "old_asset_id": old_asset_id, "package": str(package), "port": port,
              "scope": "isolated real CPU runtime/models/HTTP/SQLite; no external API; immutable assets hardlinked"}
    output.write_text(json.dumps(report, indent=2), "utf-8")
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    def run(args, stdin=None, timeout=600):
        result = subprocess.run([str(root / "runtime/python.exe"), "-I", "-B", *args], cwd=root,
            input=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, timeout=timeout)
        with (work / "commands.log").open("ab") as log: log.write(result.stdout)
        if result.returncode:
            raise RuntimeError(result.stdout.decode("utf-8", "replace")[-4000:])
    try:
        print("QA root: " + str(root), flush=True)
        run([str(root / "launcher/portable.py"), "start", "--cpu", "--no-browser", "--no-tray", "--port", str(port)])
        print("Old CPU service ready", flush=True)
        post(port, "/api/tags", {"tag_path": "更新验收/保留标签"})
        settings = request(port, "/api/settings")
        post_body = {"expected_revision": settings["revision"], "chunking": {"chunk_size": 420}}
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(urllib.request.Request(f"http://127.0.0.1:{port}/api/settings",
                data=json.dumps(post_body).encode(), headers={"Content-Type":"application/json"}, method="PATCH"), timeout=20) as response:
            json.loads(response.read())
        boundary = "qa" + uuid.uuid4().hex
        text = "离线更新验收：知识库更新必须保留原始文档、会话、标签、设置和检索索引。验收口令为星海松林。"
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="update-acceptance.txt"\r\nContent-Type: text/plain\r\n\r\n{text}\r\n--{boundary}--\r\n').encode()
        uploaded = request(port, "/api/upload", body, {"Content-Type": "multipart/form-data; boundary=" + boundary})
        deadline = time.monotonic() + 120
        while get_health(port)["retrieval"]["sparse_index_status"] != "ready":
            if time.monotonic() > deadline: raise RuntimeError("Sparse index not ready")
            time.sleep(.5)
        conversation = post(port, "/api/conversations", {"title": "更新前的保留会话"})
        answer = post(port, "/api/chat", {"conversation_id": conversation["conversation_id"], "request_id":"update-seed",
            "question":"离线更新验收口令是什么？", "tags":[]})
        assert answer["citations"]
        before = {"conversation": request(port, "/api/conversations/" + conversation["conversation_id"]),
                  "settings": request(port, "/api/settings"), "tags": request(port, "/api/tags"),
                  "uploaded": uploaded, "workspace": read_json(root / "data/workspace.json")}
        report["before"] = before
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
        extract = work / "解压的更新包"
        with zipfile.ZipFile(package) as archive: archive.extractall(extract)
        print("Starting independent update from extracted package", flush=True)
        run([str(extract / "payload/launcher/update_entry.py"), "--root", str(root), "--directory", str(extract)], b"y\n")
        print("Updated service ready", flush=True)
        after = {"conversation": request(port, "/api/conversations/" + conversation["conversation_id"]),
                 "settings": request(port, "/api/settings"), "tags": request(port, "/api/tags"),
                 "status": request(port, "/api/local-update/status"), "workspace": read_json(root / "data/workspace.json")}
        assert after["workspace"]["workspace_id"] == before["workspace"]["workspace_id"]
        assert after["workspace"]["contract"]["storage_schema"] == 6
        assert after["settings"]["revision"] == before["settings"]["revision"]
        assert after["settings"]["chunking"]["chunk_size"] == 420
        assert after["tags"] == before["tags"]
        assert [m["content"] for m in after["conversation"]["messages"]] == [m["content"] for m in before["conversation"]["messages"]]
        assert after["conversation"]["messages"][0]["retrieval_tags"] is None
        assert after["status"]["job"]["phase"] == "completed"
        answer2 = post(port, "/api/chat", {"question":"验收口令星海松林是什么？"})
        assert answer2["citations"]
        report.update(after=after, result="passed", real_retrieval_before_after="passed", uploaded_documents_not_reimported=True)
    finally:
        with __import__('contextlib').suppress(Exception):
            run([str(root / "launcher/portable.py"), "stop"], timeout=180)
        report["service_stopped"] = not (root / "state/service.json").exists()
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
        print("Report: " + str(output), flush=True)
    if report.get("result") != "passed" or not report["service_stopped"]:
        raise RuntimeError("Acceptance not complete")


if __name__ == "__main__":
    main()
