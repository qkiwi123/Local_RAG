"""Build an offline, application-only ZIP against an explicitly supported base."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from launcher import updater
from launcher.common import load_assets, validate_assets, safe_path
from deploy.windows.build_release import GUIDE


def encoded(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def entry(name: str, payload: bytes) -> dict:
    return {"path": name, "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def collect_application() -> dict[str, bytes]:
    files = {}
    for folder in ("backend", "launcher"):
        for path in sorted((REPO / folder).rglob("*")):
            if path.is_file() and path.suffix in {".py", ".ps1"} and "__pycache__" not in path.parts:
                relative = path.relative_to(REPO).as_posix()
                files[relative] = safe_path(REPO, relative).read_bytes()
    for path in sorted((REPO / "web/dist").rglob("*")):
        if path.is_file():
            relative = path.relative_to(REPO).as_posix()
            files[relative] = safe_path(REPO, relative).read_bytes()
    if "web/dist/index.html" not in files:
        raise ValueError("先构建前端，才能生成本地更新包")
    for name in ("启动.cmd", "CPU启动.cmd", "停止.cmd", "维护.cmd", "更新.cmd"):
        files[name] = (REPO / "deploy/windows" / name).read_text("utf-8").replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")
    files["使用说明.txt"] = (GUIDE + "\n本地更新：网页设置中选择应用更新ZIP，或双击更新.cmd。更新包首次接入旧版本可解压后双击安装更新.cmd。更新前正常停服并保留整套数据备份；中断时再次运行更新.cmd恢复。\n").encode("utf-8-sig")
    return files


def build(base: Path, output: Path, *, notes: str, allow_working_tree: bool = False) -> dict:
    base = base.resolve()
    output = output.absolute()
    if output.exists():
        raise ValueError("输出已存在，请更换文件名；不会覆盖历史更新包")
    if output.resolve().is_relative_to(base):
        raise ValueError("更新包输出不能位于待更新安装目录内")
    base_manifest, base_id = validate_assets(base, full=True)
    release = json.loads((base / "release-manifest.json").read_text("utf-8"))
    if release["frontend_package_lock_sha256"] != updater.digest(REPO / "web/package-lock.json"):
        raise ValueError("前端依赖已改变，需先重新装配并核对依赖和许可，不能用应用包跳过")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO, text=True))
    if dirty and not allow_working_tree:
        raise ValueError("源码未提交；内部预览需明确--allow-working-tree并记录载荷摘要")
    payload = collect_application()
    snapshot = hashlib.sha256(encoded({n: entry(n, p) for n, p in sorted(payload.items())})).hexdigest()
    version = "local-" + snapshot[:12]
    new_release = {**release, "source_commit": revision, "working_tree_included": dirty,
                   "application_snapshot_sha256": snapshot, "application_version": version,
                   "build": "Offline app update; runtime/models/licenses unchanged; internal preview"}
    payload["release-manifest.json"] = encoded(new_release)
    protected = [e for e in base_manifest["files"] if not updater.managed(e["path"])]
    target_files = sorted(protected + [entry(n, p) for n, p in payload.items()], key=lambda e: e["path"])
    payload["portable-manifest.json"] = encoded({**base_manifest, "files": target_files,
        "bytes": sum(e["size"] for e in target_files), "frontend_build_id": entry("index", payload["web/dist/index.html"])["sha256"]})
    bootstrap = {name: (REPO / "deploy/windows" / name).read_text("utf-8").replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")
                 for name in updater.BOOTSTRAP}
    manifest = {"format": 1, "kind": "local-rag-app-update", "version": version, "notes": notes,
                "internal_preview": True, "supported_base_assets": [base_id],
                "runtime_lock_sha256": updater.digest(base / "locks/w1-lock.json"),
                "supported_data_schemas": [5, 6], "target_data_schema": 6,
                "files": [entry(n, p) for n, p in sorted(payload.items())],
                "bootstrap": [entry(n, p) for n, p in sorted(bootstrap.items())],
                "source_commit": revision, "working_tree_included": dirty,
                "application_snapshot_sha256": snapshot}
    output.parent.mkdir(parents=True, exist_ok=True)
    all_files = {"update-manifest.json": encoded(manifest), **bootstrap,
                 **{"payload/" + n: p for n, p in payload.items()}}
    with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in sorted(all_files.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content, compresslevel=9)
    result = updater.inspect_package(base, output)
    result.update(zip_bytes=output.stat().st_size, zip_sha256=updater.digest(output), source_commit=revision,
                  working_tree_included=dirty, application_snapshot_sha256=snapshot)
    output.with_suffix(".json").write_bytes(encoded(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--notes", required=True)
    parser.add_argument("--allow-working-tree", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build(args.base, args.output, notes=args.notes, allow_working_tree=args.allow_working_tree), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
