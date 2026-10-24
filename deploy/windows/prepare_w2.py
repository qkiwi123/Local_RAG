"""Make a NEW isolated W2 application-validation tree from W1; not a release ZIP."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil

try:
    from .prepare_w1 import REPO, check_output
    from .probe_w1 import verify_payload
except ImportError:  # Direct script entry.
    from prepare_w1 import REPO, check_output
    from probe_w1 import verify_payload


def assemble(source: Path, output: Path) -> None:
    output = check_output(output)
    source = source.resolve()
    if not source.is_relative_to((REPO / "build").resolve()) or output.is_relative_to(source):
        raise ValueError("W2 source must be an isolated build tree, output must not nest inside it")
    print("Checking immutable W1 input...", flush=True)
    verify_payload(source)
    manifest = json.loads((source / "w1-manifest.json").read_text(encoding="utf-8"))
    if manifest["stage"] != "W1 feasibility, NOT application release":
        raise ValueError("Expected the original W1 feasibility tree")
    entries = [entry for entry in manifest["files"]
               if entry["path"].split("/")[0] in {"runtime", "models", "locks", "licenses"}]
    output.mkdir(parents=True)
    # Windows can report access-denied while sibling copy threads race to
    # create the same directory. Build the bounded directory set serially.
    parents = {Path(entry["path"]).parent for entry in entries}
    for relative in sorted(parents, key=lambda path: (len(path.parts), str(path))):
        (output / relative).mkdir(parents=True, exist_ok=True)
    def copy(entry):
        path = output / entry["path"]
        shutil.copyfile(source / entry["path"], path)
    with ThreadPoolExecutor(max_workers=8) as executor:
        for index, _ in enumerate(executor.map(copy, entries), 1):
            if index % 5000 == 0:
                print(f"Copied {index}/{len(entries)} immutable input files", flush=True)
    write_application(output, entries)
    for relative in ("reports", "temp"):
        (output / relative).mkdir()
    print(f"W2 tree prepared: {len(entries)} files; verify through bundled probe", flush=True)


def write_application(output: Path, entries: list) -> None:
    # Only reviewed source, never a repository/venv/data/.env/eval directory copy.
    application = []
    for path in sorted((REPO / "backend").rglob("*.py")):
        if "__pycache__" in path.parts or not path.resolve().is_relative_to(REPO / "backend"):
            continue
        application.append((path.relative_to(REPO).as_posix(), path))
    application += [("validation/probe_w1.py", REPO / "deploy/windows/probe_w1.py"),
                    ("validation/probe_w2.py", REPO / "deploy/windows/probe_w2.py")]
    for relative, path in application:
        target = output / relative
        if not target.resolve().is_relative_to(output):
            raise ValueError("W2 application target escaped through a directory link")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        entries.append({"path": relative, "size": target.stat().st_size,
                        "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
    payload = {"schema": 1, "stage": "W2 application validation, NOT release", "files": entries,
               "bytes": sum(entry["size"] for entry in entries)}
    (output / "w1-manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def refresh_application(output: Path) -> None:
    output = output.resolve()
    build = (REPO / "build").resolve()
    if not build.is_relative_to(REPO.resolve()) or not output.is_relative_to(build) or output == build:
        raise ValueError("Refresh only accepts an existing W2 child under project build/")
    manifest = json.loads((output / "w1-manifest.json").read_text(encoding="utf-8"))
    if manifest["stage"] != "W2 application validation, NOT release":
        raise ValueError("Not a W2 validation tree")
    print("Verifying existing W2 assets before application-only refresh...", flush=True)
    verify_payload(output)
    immutable = [entry for entry in manifest["files"] if not entry["path"].startswith(("backend/", "validation/"))]
    write_application(output, immutable)
    print("W2 application refreshed; runtime/models and data preserved", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-bundle", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--refresh-app", action="store_true")
    args = parser.parse_args()
    if args.refresh_app:
        if args.runtime_bundle is not None:
            parser.error("--refresh-app does not accept a runtime donor")
        refresh_application(args.output)
    else:
        if args.runtime_bundle is None:
            parser.error("--runtime-bundle is required for a new tree")
        assemble(args.runtime_bundle, args.output)


if __name__ == "__main__":
    main()
