"""Local files, Windows locks, asset checks and process identity for W3."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import time
import urllib.request


def safe_path(root: Path, relative: str) -> Path:
    root = root.resolve()
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts or ":" in relative:
        raise ValueError("无效的便携相对路径")
    path = root
    for part in relative_path.parts:
        path = path / part
        if path.is_symlink() or path.is_junction():
            raise ValueError(f"拒绝沿链接访问便携资产：{relative}")
    if not path.resolve().is_relative_to(root):
        raise ValueError("便携路径越界")
    return path


def root_id(root: Path) -> str:
    return hashlib.sha256(os.path.normcase(str(root.resolve())).encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("便携记录必须为JSON对象")
    return value


def write_json(root: Path, relative: str, value: dict) -> None:
    import uuid

    target = safe_path(root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}-{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def file_lock(root: Path, name: str, timeout: float = 0):
    import msvcrt

    path = safe_path(root, "state/" + name)
    path.parent.mkdir(exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("同一便携目录已有运行或维护操作，请稍后重试") from None
                time.sleep(0.1)
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def service_locked(root: Path) -> bool:
    try:
        with file_lock(root, "service.lock"):
            return False
    except RuntimeError:
        return True


def load_assets(root: Path) -> tuple[dict, str]:
    path = safe_path(root, "portable-manifest.json")
    manifest = read_json(path)
    if manifest.get("format") != 1 or manifest.get("stage") not in {
            "W3 internal prototype", "W4 internal preview"}:
        raise ValueError("缺少匹配的便携清单；不能把源码目录直接当作便携包启动")
    required = {"runtime/python.exe", "runtime/python314._pth", "backend/main.py",
                "launcher/portable.py", "web/dist/index.html", "locks/w1-lock.json"}
    names = [entry["path"] for entry in manifest["files"]]
    if len(names) != len(set(names)) or not required.issubset(names):
        raise ValueError("便携资产清单缺失或重复")
    return manifest, hashlib.sha256(path.read_bytes()).hexdigest()


def validate_assets(root: Path, *, full: bool = False) -> tuple[dict, str]:
    manifest, asset_id = load_assets(root)
    cache_path = safe_path(root, "state/verified-assets.json")
    try:
        cache = read_json(cache_path)
    except (OSError, ValueError):
        cache = {}
    previous = cache.get("mtimes", {}) if not full and cache.get("asset_id") == asset_id and cache.get("root_id") == root_id(root) else {}
    def check(entry):
        path = safe_path(root, entry["path"])
        stat = path.stat()
        if not path.is_file() or stat.st_size != entry["size"]:
            raise ValueError(f"便携资源缺失或大小不符：{entry['path']}；请重新解压完整包")
        if previous.get(entry["path"]) != stat.st_mtime_ns:
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != entry["sha256"]:
                raise ValueError(f"便携资源校验失败：{entry['path']}；请重新解压完整包")
        return entry["path"], stat.st_mtime_ns
    with ThreadPoolExecutor(max_workers=8) as executor:
        mtimes = dict(executor.map(check, manifest["files"]))
    write_json(root, "state/verified-assets.json", {"asset_id": asset_id, "root_id": root_id(root), "mtimes": mtimes})
    return manifest, asset_id


def valid_instance(value: str) -> str:
    if re.fullmatch(r"[a-f0-9]{32}", value or "") is None:
        raise ValueError("实例标识无效")
    return value


def read_record(root: Path) -> dict | None:
    path = safe_path(root, "state/service.json")
    if not path.exists():
        return None
    record = read_json(path)
    valid_instance(record["instance_id"])
    if type(record.get("port")) is not int or not 1024 <= record["port"] <= 65535:
        raise ValueError("实例端口记录无效")
    return record


def same_process(root: Path, record: dict, *, action: str = "serve", process=None) -> bool:
    import psutil

    try:
        if record["root_id"] != root_id(root):
            return False
        process = process if process is not None else psutil.Process(int(record["pid"]))
        if abs(process.create_time() - float(record["created_at"])) > 0.01:
            return False
        if Path(process.exe()).resolve() != (root / "runtime/python.exe").resolve():
            return False
        command = process.cmdline()
        expected_script = (root / "launcher/portable.py").resolve()
        if not any(Path(arg).resolve() == expected_script for arg in command if arg.endswith(".py")):
            return False
        if action == "start":
            return "start" in command
        if action not in {"serve", "tray"}:
            return False
        return (action in command and "--instance-id" in command
                and command[command.index("--instance-id") + 1] == valid_instance(record["instance_id"]))
    except (OSError, ValueError, KeyError, IndexError, psutil.Error):
        return False


def get_health(port: int) -> dict | None:
    # Never forward local status/instance data through inherited HTTP proxies.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=0.7) as response:
            data = response.read(65537)
        if len(data) > 65536:
            return None
        payload = json.loads(data)
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError):
        return None


def matches_health(root: Path, record: dict, health: dict | None) -> bool:
    if not health or not isinstance(health.get("portable"), dict):
        return False
    expected = health["portable"]
    return all(expected.get(name) == record.get(name) for name in
               ("instance_id", "root_id", "asset_id", "frontend_build_id", "pid")) and record["root_id"] == root_id(root)


def stop_path(root: Path, record: dict) -> Path:
    return safe_path(root, f"state/stop-{valid_instance(record['instance_id'])}.json")


def forget_record(root: Path, record: dict) -> None:
    current = read_record(root)
    if current and current["instance_id"] == record["instance_id"]:
        safe_path(root, "state/service.json").unlink(missing_ok=True)
    stop_path(root, record).unlink(missing_ok=True)
