"""W3 internal prototype: W1 runtime + current backend/launcher/prebuilt React."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

try:
    from .prepare_w1 import REPO
    from .prepare_w2 import assemble
    from .probe_w1 import verify_payload
except ImportError:
    from prepare_w1 import REPO
    from prepare_w2 import assemble
    from probe_w1 import verify_payload


def add_w3(output: Path) -> None:
    output = output.resolve()
    if not output.is_relative_to((REPO / "build").resolve()) or output == (REPO / "build").resolve():
        raise ValueError("W3 output must stay inside an isolated build child")
    old = json.loads((output / "w1-manifest.json").read_text(encoding="utf-8"))
    if old["stage"] != "W2 application validation, NOT release":
        raise ValueError("W3 assembly requires a fresh W2 validation tree")
    if (output / "data").exists() or (output / "portable-manifest.json").exists():
        raise ValueError("Do not upgrade an existing workspace or overwrite a W3 tree")
    verify_payload(output)
    frontend = REPO / "web/dist"
    if not (frontend / "index.html").is_file():
        raise ValueError("Build React as maintainer before W3 assembly")
    sources = [(path.relative_to(REPO).as_posix(), path) for folder in (REPO / "launcher", frontend)
               for path in sorted(folder.rglob("*")) if path.is_file() and "__pycache__" not in path.parts]
    sources += [(name, REPO / "deploy/windows" / name) for name in ("启动.cmd", "CPU启动.cmd", "停止.cmd", "维护.cmd")]
    sources += [("validation/probe_w3.py", REPO / "deploy/windows/probe_w3.py")]
    entries = list(old["files"])
    for relative, source in sources:
        if not source.resolve().is_relative_to(REPO.resolve()) or source.is_symlink() or source.is_junction():
            raise ValueError("Refusing linked application/frontend assets")
        target = output / relative
        if not target.resolve().is_relative_to(output) or target.exists():
            raise ValueError("W3 target escaped or would overwrite an existing file")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        entries.append({"path": relative, "size": target.stat().st_size,
                        "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
    frontend_build_id = hashlib.sha256((output / "web/dist/index.html").read_bytes()).hexdigest()
    result = {"format": 1, "stage": "W3 internal prototype", "frontend_build_id": frontend_build_id,
              "files": entries, "bytes": sum(entry["size"] for entry in entries)}
    (output / "portable-manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"W3 prototype prepared: {len(entries)} files; no ZIP export", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-bundle", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--refresh-app", action="store_true")
    args = parser.parse_args()
    if args.refresh_app:
        if args.runtime_bundle is not None:
            parser.error("Refresh cannot select a runtime donor")
        refresh(args.output)
    else:
        if args.runtime_bundle is None:
            parser.error("A new tree needs --runtime-bundle")
        assemble(args.runtime_bundle, args.output)
        add_w3(args.output)


def refresh(output: Path) -> None:
    output = output.resolve()
    build = (REPO / "build").resolve()
    if not build.is_relative_to(REPO.resolve()) or not output.is_relative_to(build) or output == build:
        raise ValueError("Only an isolated W3 build child can be refreshed")
    sys.path.insert(0, str(REPO))
    from launcher.common import file_lock, service_locked, validate_assets

    with file_lock(output, "command.lock"):
        if service_locked(output):
            raise RuntimeError("Stop W3 before changing its application assets")
        manifest, _ = validate_assets(output, full=True)
        immutable = [entry for entry in manifest["files"]
                     if entry["path"].split("/")[0] in {"runtime", "models", "locks", "licenses"}]
        sources = [(path.relative_to(REPO).as_posix(), path)
                   for folder in (REPO / "backend", REPO / "launcher")
                   for path in sorted(folder.rglob("*.py")) if "__pycache__" not in path.parts]
        sources += [(path.relative_to(REPO).as_posix(), path) for path in sorted((REPO / "web/dist").rglob("*")) if path.is_file()]
        sources += [(name, REPO / "deploy/windows" / name) for name in ("启动.cmd", "CPU启动.cmd", "停止.cmd", "维护.cmd")]
        sources += [("validation/" + name, REPO / "deploy/windows" / name)
                    for name in ("probe_w1.py", "probe_w2.py", "probe_w3.py")]
        old_names = {entry["path"] for entry in manifest["files"]
                     if entry["path"].split("/")[0] not in {"runtime", "models", "locks", "licenses"}}
        new_names = {name for name, _ in sources}
        if old_names - new_names:
            raise ValueError("Application files were removed; recreate the validation tree instead")
        for relative, source in sources:
            from launcher.common import safe_path

            target = safe_path(output, relative)
            if target.exists() and relative not in old_names:
                raise ValueError("Unknown file would be overwritten")
            if not source.resolve().is_relative_to(REPO.resolve()) or source.is_symlink():
                raise ValueError("Linked application source is not accepted")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            immutable.append({"path": relative, "size": target.stat().st_size,
                              "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
        manifest.update(files=immutable, bytes=sum(entry["size"] for entry in immutable),
                        frontend_build_id=hashlib.sha256((output / "web/dist/index.html").read_bytes()).hexdigest())
        (output / "portable-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("W3 app refreshed; runtime/models and data unchanged", flush=True)


if __name__ == "__main__":
    main()
