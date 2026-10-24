"""Maintainer-only, clean Windows ZIP assembly. Public release gates fail closed."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from launcher.common import safe_path

PUBLIC_GATES = ["Original wheel provenance not reconstructed from installed RECORD payloads",
                "Licensed VC Redist source and final redistribution/NOTICE review pending",
                "W5 clean Windows, full system-write and complete product acceptance pending"]
ENTRIES = ("启动.cmd", "CPU启动.cmd", "停止.cmd", "维护.cmd")
GUIDE = """个人 RAG 知识库 · Windows x64 内部验证包

这是本机验证预览，不是已完成跨机和再分发许可审核的公开发行版。
解压到有写入权限的普通目录，完整保留所有文件。路径支持中文和空格。
1. 双击 启动.cmd，优先使用 NVIDIA GPU；不可用时询问是否用 CPU。
2. CPU启动.cmd 明确使用 CPU，保留混合检索和 Reranker。
3. 就绪后自动打开 React 设置页，填写自己的模型 API Key 并启用生成/视觉。
4. 关闭网页不会停止后台；请运行 停止.cmd，看到停止完成后再移动/备份/删除。
5. 超时后可确认强制停止；正在上传时强制停止可能使该份文档入库失败，重启后重新上传。

data 包含知识库数据库、上传副本、会话、设置和密钥密文。
models/runtime 为随包模型和运行环境；cache/temp/logs/state 也在本目录。
维护.cmd 只清理明确列出的可重建缓存，不删除 data、模型或未知文件。
移除：停止 → 按需备份到目录外 → 手动删除整个目录。不要覆盖运行中的旧版本。
升级前备份整个目录；只在运行依赖/模型/数据库合同兼容时迁移 data。
Key 使用当前 Windows 用户加密，换电脑/账号需重新填写。
浏览器外观/尺寸/会话标识、缓存和历史由浏览器管理，可能写入 AppData；
系统/驱动也可能留下记录。本应用不安装服务、自启项或修改防火墙/注册表。

默认仅本机 127.0.0.1:8000；端口被其他程序占用时会报错，不结束其他程序。
日志在 logs，启动/停止错误请保留日志。不要对个人资料运行开发验收脚本。
"""


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def new_stage(path: Path) -> Path:
    path = path.absolute()
    build = (REPO / "build/windows-release").resolve()
    if not path.resolve().is_relative_to(build) or path.resolve() == build or path.exists():
        raise ValueError("Staging requires a NEW isolated build/windows-release child")
    safe_path(REPO, path.relative_to(REPO).as_posix())
    path.mkdir(parents=True)
    return path.resolve()


def copy_file(source: Path, stage: Path, relative: str):
    if source.is_symlink() or source.is_junction() or not source.is_file():
        raise ValueError("Linked or missing source: " + str(source))
    target = safe_path(stage, relative)
    if target.exists():
        raise ValueError("Duplicate assembly file: " + relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def build_frontend(work: Path) -> Path:
    if work.exists():
        raise ValueError("Frontend build directory already exists")
    work.mkdir(parents=True)
    tracked = subprocess.check_output(["git", "ls-files", "-z", "--", "web"], cwd=REPO).decode().split("\0")
    for name in filter(None, tracked):
        relative = Path(name).relative_to("web").as_posix()
        if relative.startswith(("dist/", "node_modules/")) or Path(relative).name.startswith(".env"):
            continue
        source = safe_path(REPO, name)
        copy_file(source, work, relative)
    npm = shutil.which("npm.cmd")
    if not npm:
        raise ValueError("Maintainer Node/npm is required; end users never need npm")
    for args in ([npm, "ci", "--no-audit", "--no-fund"], [npm, "run", "build"]):
        subprocess.run(args, cwd=work, check=True, timeout=600)
    if not (work / "dist/index.html").is_file():
        raise ValueError("React build did not produce index.html")
    return work


def license_inventory(stage: Path, frontend: Path, relocated: dict):
    records = []
    for dist in sorted(importlib.metadata.distributions(path=[str(stage / "runtime/Lib/site-packages")]),
                       key=lambda d: d.metadata.get("Name", "").lower()):
        files = [str(p).replace("\\", "/") for p in (dist.files or [])
                 if any(t in str(p).lower() for t in ("license", "notice", "copying"))]
        license_paths = [relocated.get("runtime/Lib/site-packages/" + p, "runtime/Lib/site-packages/" + p) for p in files]
        records.append({"name": dist.metadata["Name"], "version": dist.version,
                        "license": dist.metadata.get("License-Expression") or dist.metadata.get("License") or "unresolved",
                        "license_files_in_package": [p for p in license_paths if (stage / p).is_file()]})
    frontend_records = []
    lock = json.loads((frontend / "package-lock.json").read_text("utf-8"))
    for relative, info in sorted(lock.get("packages", {}).items()):
        if not relative or "node_modules/" not in relative:
            continue
        package = safe_path(frontend, relative)
        if not package.is_dir():  # Optional dependencies for other platforms.
            continue
        number = f"{len(frontend_records):04}"
        copied = []
        for source in sorted(package.iterdir()):
            if source.is_file() and source.name.lower().startswith(("license", "notice", "copying")):
                target = f"licenses/frontend/{number}/{source.name}"
                copy_file(source, stage, target)
                copied.append(target)
        frontend_records.append({"package": relative, "version": info.get("version"),
                                 "license": info.get("license", "unresolved"), "license_files": copied})
    dump(stage / "licenses/THIRD_PARTY_INVENTORY.json", {
        "scope": "Inventory and preserved texts, not redistribution approval",
        "python": records, "frontend_build_and_runtime": frontend_records,
        "pending_gates": PUBLIC_GATES})


def package_path(relative: str, ordinal: int) -> str:
    # Preserve deep third-party license bytes without requiring Windows global
    # long-path settings. Executable/import resource paths are never relocated.
    if len(relative) > 150 and "/licenses/" in relative.lower():
        return f"licenses/relocated/{ordinal:05d}-{Path(relative).name}"
    if len(relative) > 150:
        raise ValueError("Runtime path exceeds portable layout budget: " + relative)
    return relative


def assemble(donor: Path, stage: Path, frontend: Path, *, source_commit: str):
    donor = donor.resolve()
    if not donor.is_relative_to((REPO / "build").resolve()):
        raise ValueError("Runtime donor must be the isolated W1 build tree")
    m = json.loads(safe_path(donor, "w1-manifest.json").read_text("utf-8"))
    if m.get("stage") != "W1 feasibility, NOT application release":
        raise ValueError("Expected immutable W1 runtime/model donor")
    entries = [x for x in m["files"] if x["path"].split("/")[0] in {"runtime", "models", "locks", "licenses"}]
    if len({x["path"] for x in entries}) != len(entries):
        raise ValueError("Duplicate runtime donor entries")
    # Verify the locked immutable payload; never recursively copy donor data.
    def verify(entry):
        source = safe_path(donor, entry["path"])
        if source.stat().st_size != entry["size"] or digest(source) != entry["sha256"]:
            raise ValueError("Runtime donor drift: " + entry["path"])
    print("W4: verifying immutable runtime/model input...", flush=True)
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(verify, entries))
    copies = [(entry, package_path(entry["path"], index)) for index, entry in enumerate(entries, 1)]
    relocated = {entry["path"]: relative for entry, relative in copies if relative != entry["path"]}
    # Pre-create shared parents serially, then copy independent files in parallel.
    parents = {Path(relative).parent.as_posix() for _, relative in copies}
    for parent in sorted(parents, key=lambda p: (len(Path(p).parts), p)):
        safe_path(stage, parent).mkdir(parents=True, exist_ok=True)
    def copy(item):
        entry, relative = item
        copy_file(safe_path(donor, entry["path"]), stage, relative)
    with ThreadPoolExecutor(max_workers=8) as executor:
        for index, _ in enumerate(executor.map(copy, copies), 1):
            if index % 10000 == 0:
                print(f"W4: copied {index}/{len(entries)} runtime/model files", flush=True)
    for folder, pattern in (("backend", "*.py"), ("launcher", "*.py")):
        for path in sorted((REPO / folder).rglob(pattern)):
            if "__pycache__" not in path.parts:
                copy_file(safe_path(REPO, path.relative_to(REPO).as_posix()), stage, path.relative_to(REPO).as_posix())
    for path in sorted((frontend / "dist").rglob("*")):
        if path.is_file():
            copy_file(safe_path(frontend, path.relative_to(frontend).as_posix()), stage,
                      "web/" + path.relative_to(frontend).as_posix())
    for name in ENTRIES:
        # CMD requires CRLF on Windows, independent of checkout line endings.
        text = (REPO / "deploy/windows" / name).read_text("utf-8")
        (stage / name).write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
    (stage / "使用说明.txt").write_text(GUIDE, encoding="utf-8-sig")
    dump(stage / "licenses/RELOCATED_PATHS.json", {"scope": "Only deeply nested license texts; bytes unchanged",
         "files": [{"original_path": old, "package_path": new, "sha256": digest(stage / new)} for old, new in relocated.items()]})
    license_inventory(stage, frontend, relocated)
    dump(stage / "release-manifest.json", {
        "format": 1, "stage": "W4 internal preview", "public_release_approved": False,
        "source_commit": source_commit, "runtime_lock_sha256": digest(stage / "locks/w1-lock.json"),
        "frontend_package_lock_sha256": digest(frontend / "package-lock.json"),
        "pending_gates": PUBLIC_GATES, "build": "Maintainer npm ci/build; pinned RECORD-vendored runtime/models; no user data"})
    files = [{"path": p.relative_to(stage).as_posix(), "size": p.stat().st_size, "sha256": digest(p)}
             for p in sorted(stage.rglob("*")) if p.is_file()]
    dump(stage / "portable-manifest.json", {"format": 1, "stage": "W4 internal preview", "files": files,
         "bytes": sum(x["size"] for x in files), "frontend_build_id": digest(stage / "web/dist/index.html")})
    assert_clean_payload(stage)


def assert_clean_payload(stage: Path):
    manifest = json.loads((stage / "portable-manifest.json").read_text("utf-8"))
    expected = {x["path"] for x in manifest["files"]} | {"portable-manifest.json"}
    actual = {p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file()}
    if actual != expected:
        raise ValueError("Unexpected or missing files in release tree")
    for name in actual:
        safe_path(stage, name)
        if name.split("/")[0] in {"data", "cache", "temp", "logs", "state", "reports", "eval", ".git", "validation"} or Path(name).name.startswith(".env"):
            raise ValueError("Private/runtime material in release tree: " + name)
    def verify(entry):
        path = safe_path(stage, entry["path"])
        if path.stat().st_size != entry["size"] or digest(path) != entry["sha256"]:
            raise ValueError("Release asset changed after manifest: " + entry["path"])
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(verify, manifest["files"]))


def export_zip(stage: Path, output: Path) -> dict:
    assert_clean_payload(stage)
    output = output.absolute()
    if output.exists() or output.with_suffix(".zip.json").exists() or output.with_suffix(".zip.partial").exists() or output.is_symlink():
        raise ValueError("ZIP output already exists; choose a new name")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".zip.partial")
    files = sorted(p for p in stage.rglob("*") if p.is_file())
    with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as archive:
        for index, source in enumerate(files, 1):
            info = zipfile.ZipInfo(stage.name + "/" + source.relative_to(stage).as_posix(), (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.compress_level = 1
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            with source.open("rb") as reader, archive.open(info, "w", force_zip64=True) as writer:
                shutil.copyfileobj(reader, writer, 1024 * 1024)
            if index % 10000 == 0:
                print(f"W4: ZIP {index}/{len(files)} files", flush=True)
    with zipfile.ZipFile(temporary) as archive:
        if archive.testzip() is not None:
            raise ValueError("ZIP CRC validation failed; partial retained")
    # Windows rename refuses an existing destination, preserving prior exports.
    temporary.rename(output)
    result = {"format": 1, "stage": "W4 internal preview", "zip_name": output.name,
              "zip_sha256": digest(output), "zip_bytes": output.stat().st_size,
              "files": len(files), "portable_manifest_sha256": digest(stage / "portable-manifest.json"),
              "zip_crc_verified": True, "pending_gates": PUBLIC_GATES}
    dump(output.with_suffix(".zip.json"), result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-bundle", type=Path, default=REPO / "build/windows-w1/中文 空格 验证包")
    parser.add_argument("--output-directory", type=Path)
    parser.add_argument("--name")
    parser.add_argument("--internal-preview", action="store_true")
    args = parser.parse_args()
    if not args.internal_preview:
        parser.error("Public release blocked: " + "; ".join(PUBLIC_GATES) + ". Use --internal-preview for local validation only.")
    # Never label uncommitted application files as a clean source revision.
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO).strip()
    if dirty:
        parser.error("Commit the source changes before producing a traceable ZIP")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO).decode().strip()
    name = args.name or "LocalRAG-win64-preview-" + commit[:8]
    if not name or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for c in name) or name in {".", ".."}:
        parser.error("Package name must use plain letters, numbers, dash, underscore or dot")
    output_directory = args.output_directory
    if output_directory is None:
        preference = REPO / "build/windows-release/output-preference.json"
        output_directory = Path(json.loads(preference.read_text("utf-8"))["output_directory"]) if preference.exists() else REPO / "build/windows-release/exports"
    output = output_directory / (name + ".zip")
    if output.exists() or output.with_suffix(".zip.json").exists():
        parser.error("Existing export is preserved; choose a new --name")
    stage = new_stage(REPO / "build/windows-release" / name / "RAG")
    frontend = build_frontend(REPO / "build/windows-release" / (name + "-frontend"))
    assemble(args.runtime_bundle, stage, frontend, source_commit=commit)
    print(json.dumps(export_zip(stage, output), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
