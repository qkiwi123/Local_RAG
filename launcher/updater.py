"""Offline application-only updates. No model/runtime changes or network downloads.

The journal and verified backups stay outside data. Workers run from a frozen
copy so the installed launcher can be replaced without replacing the worker.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
import uuid
import zipfile

from launcher.common import (safe_path, read_json, write_json, file_lock, load_assets,
                            validate_assets, read_record, same_process, service_locked)

MAX_ZIP_BYTES = 64 * 1024 * 1024
MAX_PAYLOAD_BYTES = 256 * 1024 * 1024
TERMINAL = {"completed", "rolled_back", "rejected"}
APP_FILES = {"启动.cmd", "CPU启动.cmd", "停止.cmd", "维护.cmd", "更新.cmd", "使用说明.txt",
             "release-manifest.json", "portable-manifest.json"}
BOOTSTRAP = {"安装更新.cmd", "安装更新.ps1"}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def managed(name: str) -> bool:
    return name in APP_FILES or name.startswith(("backend/", "launcher/", "web/dist/"))


def normalized(name: str) -> str:
    # ZIP names are platform independent; reject Windows aliases/ADS as well.
    devices = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)}
    if (not name or "\\" in name or name.startswith("/") or any(c in name for c in ':<>"|?*')
            or any(p in {"", ".", ".."} or p.endswith((".", " ")) or p.split('.')[0].upper() in devices
                   for p in name.split("/"))):
        raise ValueError("更新包包含无效路径")
    return name


def job_path(root: Path, job_id: str) -> Path:
    if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
        raise ValueError("无效更新记录")
    return safe_path(root, f"state/updates/{job_id}")


def journal(root: Path, job_id: str, **changes) -> dict:
    path = job_path(root, job_id) / "journal.json"
    state = read_json(path)
    state.update(changes, updated_at=time.time())
    write_json(root, path.relative_to(root).as_posix(), state)
    marker = safe_path(root, "state/update-active.json")
    if state["phase"] in TERMINAL and marker.exists() and read_json(marker).get("id") == job_id:
        marker.unlink()
    return state


def active(root: Path) -> dict | None:
    path = safe_path(root, "state/update-active.json")
    if not path.exists():
        return None
    marker = read_json(path)
    state = read_json(job_path(root, marker["id"]) / "journal.json")
    return None if state["phase"] in TERMINAL else state


def guard_start(root: Path, update_id: str | None = None) -> None:
    state = active(root)
    if state and state["id"] != update_id:
        raise RuntimeError("检测到尚未完成的更新，请运行更新.cmd并选择恢复，暂不启动混合版本")


def _zip_manifest(package: Path) -> tuple[dict, dict[str, bytes]]:
    if package.stat().st_size > MAX_ZIP_BYTES:
        raise ValueError("更新ZIP过大；请选择应用更新包，而非完整安装包")
    with zipfile.ZipFile(package) as archive:
        entries = archive.infolist()
        if len(entries) > 1200 or sum(i.file_size for i in entries) > MAX_PAYLOAD_BYTES:
            raise ValueError("更新包展开后超出应用更新限制")
        names = [normalized(i.filename) for i in entries]
        if len({n.casefold() for n in names}) != len(names):
            raise ValueError("更新包存在重复路径")
        if any(i.is_dir() or (i.external_attr >> 16) & 0o170000 == 0o120000 for i in entries):
            raise ValueError("更新包不能包含目录条目或链接")
        raw = archive.read("update-manifest.json")
        if len(raw) > 1024 * 1024:
            raise ValueError("更新说明过大")
        manifest = json.loads(raw)
        if manifest.get("format") != 1 or manifest.get("kind") != "local-rag-app-update":
            raise ValueError("不是本项目的应用更新包")
        expected = {"update-manifest.json"}
        blobs = {}
        seen = set()
        for entry in manifest["files"]:
            name = normalized(entry["path"])
            if name.casefold() in seen or not managed(name):
                raise ValueError("更新内容重复或试图改写数据、模型、运行环境")
            seen.add(name.casefold())
            payload = archive.read("payload/" + name)
            if len(payload) != entry["size"] or hashlib.sha256(payload).hexdigest() != entry["sha256"]:
                raise ValueError("更新文件校验失败：" + name)
            blobs[name] = payload
            expected.add("payload/" + name)
        for entry in manifest["bootstrap"]:
            if entry["path"] not in BOOTSTRAP:
                raise ValueError("未知更新入口")
            payload = archive.read(entry["path"])
            if len(payload) != entry["size"] or hashlib.sha256(payload).hexdigest() != entry["sha256"]:
                raise ValueError("更新入口校验失败")
            expected.add(entry["path"])
        if set(names) != expected or not APP_FILES.issubset(blobs):
            raise ValueError("更新包文件缺失或包含未声明文件")
    return manifest, blobs


def inspect_package(root: Path, package: Path) -> dict:
    package_hash = digest(package)
    manifest, blobs = _zip_manifest(package)
    base, asset_id = load_assets(root)
    if asset_id not in manifest["supported_base_assets"]:
        raise ValueError("此更新包不支持当前安装版本，请选择匹配的更新包")
    if digest(safe_path(root, "locks/w1-lock.json")) != manifest["runtime_lock_sha256"]:
        raise ValueError("运行环境或模型版本不匹配，此包不更新这些组件")
    target = json.loads(blobs["portable-manifest.json"])
    if target.get("format") != 1 or target.get("stage") != base["stage"]:
        raise ValueError("目标资源清单不匹配")
    old = {normalized(e["path"]): e for e in base["files"]}
    new = {normalized(e["path"]): e for e in target["files"]}
    if len(old) != len(base["files"]) or len(new) != len(target["files"]):
        raise ValueError("资源清单路径重复")
    if {n: e for n, e in old.items() if not managed(n)} != {n: e for n, e in new.items() if not managed(n)}:
        raise ValueError("应用更新包不能改变模型、依赖或许可资产")
    if set(blobs) != {n for n in new if managed(n)} | {"portable-manifest.json"}:
        raise ValueError("更新载荷与目标资源清单不一致")
    for name, payload in blobs.items():
        safe_path(root, name)
        if name != "portable-manifest.json" and (new[name]["size"] != len(payload)
                or new[name]["sha256"] != hashlib.sha256(payload).hexdigest()):
            raise ValueError("目标资源摘要不一致")
    if target["frontend_build_id"] != hashlib.sha256(blobs["web/dist/index.html"]).hexdigest():
        raise ValueError("前端版本标识错误")
    if hashlib.sha256(blobs["portable-manifest.json"]).hexdigest() == asset_id:
        raise ValueError("当前已经是这个版本，无需重复更新")
    workspace_path = safe_path(root, "data/workspace.json")
    if workspace_path.exists():
        workspace = read_json(workspace_path)
        if workspace["contract"]["runtime_lock_sha256"] != manifest["runtime_lock_sha256"]:
            raise ValueError("数据工作空间与运行环境不匹配")
        database = safe_path(root, "data/metadata.db")
        if database.exists():
            connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
            try:
                versions = connection.execute("SELECT schema_version FROM schema_meta").fetchall()
            finally:
                connection.close()
            if len(versions) != 1 or versions[0][0] not in manifest["supported_data_schemas"]:
                raise ValueError("数据库版本不在该更新包支持范围")
    elif safe_path(root, "data").exists() and any(safe_path(root, "data").iterdir()):
        raise ValueError("已有数据缺少便携标识，拒绝接管")
    if digest(package) != package_hash:
        raise ValueError("检查期间更新包发生变化")
    return {"version": manifest["version"], "notes": manifest["notes"],
            "base_asset_id": asset_id, "target_asset_id": hashlib.sha256(blobs["portable-manifest.json"]).hexdigest(),
            "package_sha256": package_hash, "files": len(blobs), "bytes": sum(map(len, blobs.values())),
            "data_schema": manifest["target_data_schema"], "internal_preview": manifest["internal_preview"]}


def stage(root: Path, package: Path) -> dict:
    if active(root):
        raise RuntimeError("已有更新尚未完成，请先恢复")
    info = inspect_package(root, package)
    job_id = uuid.uuid4().hex
    directory = job_path(root, job_id)
    directory.mkdir(parents=True)
    shutil.copyfile(package, directory / "package.zip")
    if digest(directory / "package.zip") != info["package_sha256"]:
        raise ValueError("复制更新包时内容发生变化")
    state = {"id": job_id, "phase": "staged", "message": "更新包已检查，等待确认", **info}
    write_json(root, directory.relative_to(root).as_posix() + "/journal.json", state)
    return state


def _copy_tree(source: Path, destination: Path) -> dict[str, dict]:
    result = {}
    if not source.exists():
        destination.mkdir(parents=True, exist_ok=True)
        return result
    if source.is_symlink() or source.is_junction():
        raise ValueError("备份拒绝目录链接")
    destination.mkdir(parents=True, exist_ok=True)
    for folder, directories, files in os.walk(source, followlinks=False):
        safe_path(destination, Path(folder).relative_to(source).as_posix()).mkdir(parents=True, exist_ok=True)
        for name in directories + files:
            candidate = Path(folder) / name
            if candidate.is_symlink() or candidate.is_junction():
                raise ValueError("备份拒绝沿数据链接访问外部文件")
        for name in files:
            original = Path(folder) / name
            relative = original.relative_to(source).as_posix()
            target = safe_path(destination, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
            entry = {"size": original.stat().st_size, "sha256": digest(original)}
            if target.stat().st_size != entry["size"] or digest(target) != entry["sha256"]:
                raise ValueError("备份校验失败：" + relative)
            result[relative] = entry
    return result


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".update-" + uuid.uuid4().hex + ".tmp")
    try:
        shutil.copyfile(source, temporary)
        with temporary.open("rb+") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _backup(root: Path, job_id: str, names: set[str]) -> None:
    directory = job_path(root, job_id)
    available = shutil.disk_usage(root).free
    data_size = 0
    for folder, directories, files in os.walk(safe_path(root, "data"), followlinks=False):
        for name in directories + files:
            path = Path(folder) / name
            if path.is_symlink() or path.is_junction():
                raise ValueError("数据中存在目录或文件链接，未开始替换")
        data_size += sum((Path(folder) / name).stat().st_size for name in files)
    if available < data_size * 2 + 64 * 1024 * 1024:
        raise RuntimeError("剩余空间不足以备份和恢复现有数据，未开始替换")
    old = {}
    for name in sorted(names):
        source = safe_path(root, name)
        if source.exists():
            target = safe_path(directory, "backup/app/" + name)
            _atomic_copy(source, target)
            old[name] = {"size": source.stat().st_size, "sha256": digest(source)}
            if digest(target) != old[name]["sha256"]:
                raise ValueError("程序备份校验失败")
    data = _copy_tree(safe_path(root, "data"), directory / "backup/data")
    write_json(root, directory.relative_to(root).as_posix() + "/backup/manifest.json", {"app": old, "data": data})
    journal(root, job_id, phase="backed_up", message="程序与整套数据备份已校验", backup_complete=True, names=sorted(names))


def _check_backup(directory: Path, group: str, entries: dict) -> None:
    for name, entry in entries.items():
        path = safe_path(directory, "backup/" + group + "/" + name)
        if path.stat().st_size != entry["size"] or digest(path) != entry["sha256"]:
            raise RuntimeError("备份已损坏，停止自动恢复以保留现场")


def rollback(root: Path, job_id: str) -> None:
    from launcher.portable import stop

    directory = job_path(root, job_id)
    state = read_json(directory / "journal.json")
    if not state.get("backup_complete"):
        journal(root, job_id, phase="rolled_back", message="尚未替换程序或数据；若项目已停止，请重新启动")
        return
    record = read_record(root)
    if record and same_process(root, record):
        stop(root, expected_instance_id=record["instance_id"], timeout=120, allow_prompt=False)
    if service_locked(root):
        raise RuntimeError("服务仍持有文件，暂不恢复，请正常停止后再次运行更新.cmd")
    with file_lock(root, "command.lock", timeout=5):
        state = journal(root, job_id, phase="rolling_back", message="正在恢复原程序与备份数据")
        if state.get("backup_complete"):
            backup = read_json(directory / "backup/manifest.json")
            _check_backup(directory, "app", backup["app"])
            _check_backup(directory, "data", backup["data"])
            if state.get("data_may_change") and not state.get("data_restore_complete"):
                restore = directory / ("restore-" + uuid.uuid4().hex)
                _copy_tree(directory / "backup/data", restore)
                current = safe_path(root, "data")
                if current.exists():
                    current.rename(directory / ("failed-data-" + uuid.uuid4().hex))
                restore.rename(current)
                # Persist BEFORE restoring the legacy launcher. If recovery is
                # interrupted later, an already recovered data tree must not be
                # rewound a second time (the legacy app may have reopened it).
                journal(root, job_id, data_restore_complete=True)
            for name in state["names"]:
                target = safe_path(root, name)
                if name in backup["app"]:
                    _atomic_copy(safe_path(directory, "backup/app/" + name), target)
                elif target.exists():
                    retired = safe_path(directory, "failed-app/" + uuid.uuid4().hex + "/" + name)
                    retired.parent.mkdir(parents=True, exist_ok=True)
                    target.rename(retired)
        if state.get("backup_complete"):
            validate_assets(root, full=True)
        journal(root, job_id, phase="rolled_back", message="已恢复原版本和对应数据，请重新启动项目")


def run_update(root: Path, job_id: str, *, restart: bool = True) -> dict:
    from launcher.portable import start, stop

    directory = job_path(root, job_id)
    with file_lock(root, "update.lock", timeout=2):
        state = read_json(directory / "journal.json")
        if state["phase"] in TERMINAL:
            return state
        if state["phase"] != "queued":
            rollback(root, job_id)
            return read_json(directory / "journal.json")
        try:
            info = inspect_package(root, directory / "package.zip")
            if info["package_sha256"] != state["package_sha256"]:
                raise ValueError("待更新的包已被修改")
            manifest, payload = _zip_manifest(directory / "package.zip")
            old_manifest, _ = validate_assets(root, full=True)
            old_record = read_record(root)
            journal(root, job_id, phase="stopping", message="正在等待任务完成并正常停止项目", old_record=old_record)
            stop(root, expected_instance_id=old_record["instance_id"] if old_record else None,
                 timeout=120, allow_prompt=False)
            if service_locked(root):
                raise RuntimeError("项目未完全停止，未替换文件")
            with file_lock(root, "command.lock", timeout=5):
                # Recheck after the service has released SQLite/Chroma handles.
                inspect_package(root, directory / "package.zip")
                names = {e["path"] for e in old_manifest["files"] if managed(e["path"])} | set(payload)
                journal(root, job_id, phase="backing_up", message="正在备份程序、文档、索引、会话与设置")
                _backup(root, job_id, names)
                journal(root, job_id, phase="applying", message="正在替换应用文件")
                # Recovery entry and guard first; data/runtime/models never appear here.
                first = ["launcher/updater.py", "launcher/update_entry.py", "launcher/update_ui.ps1", "更新.cmd", "launcher/portable.py"]
                order = [n for n in first if n in payload] + sorted(set(payload) - set(first) - {"portable-manifest.json"}) + ["portable-manifest.json"]
                for name in order:
                    staged = safe_path(directory, "payload/" + name)
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    staged.write_bytes(payload[name])
                    _atomic_copy(staged, safe_path(root, name))
                for name in names - set(payload):
                    current = safe_path(root, name)
                    if current.exists():
                        retired = safe_path(directory, "retired/" + name)
                        retired.parent.mkdir(parents=True, exist_ok=True)
                        current.rename(retired)
                validate_assets(root, full=True)
            journal(root, job_id, phase="starting", message="正在启动新版并核对运行状态", data_may_change=True)
            if restart:
                record = start(root, cpu=bool(old_record and old_record["device"] == "cpu"),
                               port=old_record["port"] if old_record else None, no_browser=True,
                               accept_cpu_fallback=bool(old_record and old_record["device"] == "cpu"),
                               update_id=job_id)
                if record["asset_id"] != info["target_asset_id"]:
                    raise RuntimeError("启动的版本与更新包不一致")
                database = safe_path(root, "data/metadata.db")
                connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
                try:
                    if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
                        raise RuntimeError("更新后的数据库完整性检查失败")
                finally:
                    connection.close()
            return journal(root, job_id, phase="completed", message="更新完成，原有数据已保留，请刷新页面",
                           restart=restart)
        except Exception as error:
            journal(root, job_id, error=str(error), message="更新未完成，正在恢复")
            try:
                rollback(root, job_id)
            except Exception as recovery_error:
                journal(root, job_id, phase="recovery_required", message="恢复尚未完成，请运行更新.cmd继续恢复",
                        recovery_error=str(recovery_error))
            return read_json(directory / "journal.json")


def queue(root: Path, job_id: str, *, launch: bool = True) -> dict:
    directory = job_path(root, job_id)
    with file_lock(root, "update.lock", timeout=2):
        running = active(root)
        if running:
            if running["id"] == job_id:
                return running
            raise RuntimeError("已有更新正在进行")
        state = read_json(directory / "journal.json")
        if state["phase"] != "staged":
            raise ValueError("该更新记录不能再次执行")
        checked = inspect_package(root, directory / "package.zip")
        if checked["package_sha256"] != state["package_sha256"]:
            raise ValueError("确认前更新包已变化，请重新选择")
        # Use trusted currently loaded code; never execute staged package code for inspection.
        source = Path(__file__).resolve().parent
        runner = directory / "runner/launcher"
        runner.mkdir(parents=True)
        for name in ("__init__.py", "common.py", "portable.py", "updater.py", "update_entry.py"):
            shutil.copyfile(source / name, runner / name)
        write_json(root, "state/update-active.json", {"id": job_id})
        write_json(root, "state/update-latest.json", {"id": job_id})
        state = journal(root, job_id, phase="queued", message="更新已确认，即将停止并升级")
        if launch:
            try:
                with (directory / "worker.log").open("ab") as output:
                    subprocess.Popen([str(root / "runtime/python.exe"), "-I", "-B", str(runner / "update_entry.py"),
                                      "--root", str(root), "--job", job_id], cwd=root,
                                     stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception as error:
                journal(root, job_id, phase="rejected", message="更新器未能启动，程序与数据未替换", error=str(error))
                raise
        return state
