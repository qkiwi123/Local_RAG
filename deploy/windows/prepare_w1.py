"""W1 only: freeze/vend verified installed distributions into embedded CPython.

No backend imports, pip installs, system configuration, or production-data writes.
The donor is used only at build time; RECORD files, not the venv directory, define
the payload. Original wheel provenance is NOT implied by installed-file hashes.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import base64
import hashlib
import importlib.metadata as metadata
import json
import io
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import zipfile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PYTHON = {
    "version": "3.14.0",
    "url": "https://www.python.org/ftp/python/3.14.0/python-3.14.0-embed-amd64.zip",
    "sha256": "8d4d3590c10449d78aa4375f534e6d5f3027d67fdc362dd1a882279db6f90fdf",
    "digest_source": "official zip.sigstore messageSignature.messageDigest (signature not independently verified)",
}
MODELS = {
    "embedding": {
        "id": "BAAI/bge-large-zh-v1.5",
        "revision": "79e7739b6ab944e86d6171e44d24c997fc1e0116",
        "license": "MIT",
        "files": ["config.json", "config_sentence_transformers.json", "modules.json",
                  "pytorch_model.bin", "README.md", "sentence_bert_config.json",
                  "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json",
                  "vocab.txt", "1_Pooling/config.json"],
    },
    "reranker": {
        "id": "BAAI/bge-reranker-v2-m3",
        "revision": "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e",
        "license": "Apache-2.0",
        "files": ["config.json", "model.safetensors", "README.md",
                  "sentencepiece.bpe.model", "special_tokens_map.json",
                  "tokenizer.json", "tokenizer_config.json"],
    },
}
EXCLUDED_ROOTS = {"streamlit", "pytest", "pytest-asyncio"}
NATIVE_SUPPORT = ["msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
                  "msvcp140_atomic_wait.dll", "msvcp140_codecvt_ids.dll", "concrt140.dll"]


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def relative_path(value: str) -> Path:
    """Reject traversal/drive paths before any assembly write."""
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or ":" in value or not path.parts:
        raise ValueError(f"Unsafe payload path: {value}")
    return Path(*path.parts)


def dependency_closure(roots: list[str]) -> dict:
    selected, extras = {}, {}
    pending = [Requirement(root) for root in roots]
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        dist = metadata.distribution(name)
        if not requirement.specifier.contains(dist.version, prereleases=True):
            raise ValueError(f"Incompatible installed dependency: {requirement} / {dist.version}")
        requested = set(requirement.extras) | {""}
        new_extras = requested - extras.get(name, set())
        if name in selected and not new_extras:
            continue
        selected[name] = dist
        extras.setdefault(name, set()).update(requested)
        for value in dist.requires or []:
            child = Requirement(value)
            if child.marker is None or any(child.marker.evaluate({"extra": extra}) for extra in new_extras):
                pending.append(child)
    return dict(sorted(selected.items()))


def distribution_files(dist):
    """Include recorded resources/DLLs/licenses; never copy venv launchers/pyc."""
    root = Path(dist.locate_file("")).resolve()
    if not dist.files:
        raise ValueError(f"Missing RECORD: {dist.metadata['Name']}")
    for item in sorted(dist.files, key=str):
        name = str(item).replace("\\", "/")
        if name.startswith("../../Scripts/") or name.startswith("../../../Scripts/"):
            continue
        if name.startswith("../../share/man/"):
            # Installed CLI manual pages are not runtime resources; never copy
            # them to the user's global share directory.
            continue
        relative = relative_path(name)
        if "__pycache__" in relative.parts or relative.suffix == ".pyc":
            continue
        if relative.suffix == ".pth":
            # Embedded ._pth explicitly defines all module paths; do not execute
            # arbitrary site hooks or import a global/editable environment.
            continue
        source = root / relative
        if not source.resolve().is_relative_to(root) or not source.is_file():
            raise ValueError(f"Missing/escaping distribution resource: {name}")
        digest = sha256(source)
        if item.hash is not None:
            if item.hash.mode != "sha256":
                raise ValueError(f"Unsupported RECORD hash: {name}")
            actual = base64.urlsafe_b64encode(bytes.fromhex(digest)).rstrip(b"=").decode()
            if actual != item.hash.value:
                raise ValueError(f"RECORD hash mismatch: {dist.metadata['Name']} / {name}")
        yield relative, source, {"path": relative.as_posix(), "size": source.stat().st_size, "sha256": digest}


def fingerprint(files: list[dict]) -> dict:
    return {
        "files": len(files), "bytes": sum(item["size"] for item in files),
        "payload_sha256": hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    }


def roots_from_requirements() -> list[str]:
    roots = []
    for line in (REPO / "requirements.txt").read_text(encoding="utf-8").splitlines():
        value = line.split("#", 1)[0].strip()
        if value and canonicalize_name(Requirement(value).name) not in EXCLUDED_ROOTS:
            roots.append(value)
    roots.append("torch==2.13.0+cu130")
    return roots


def support_files(archive: Path):
    """Small build-time support assets, NEVER a whole base Python/venv copy.

    The .py counterparts of embedded modules allow PyTorch inspect/JIT to read
    standard-library source. App-local MSVC avoids borrowing installed redists.
    Redistributable provenance/licensing is a separate W4 distribution gate.
    """
    if sha256(archive) != PYTHON["sha256"] or platform_version() != PYTHON["version"]:
        raise ValueError("Support assets require validated embedded ZIP and matching CPython donor")
    with zipfile.ZipFile(archive) as zipped:
        with zipfile.ZipFile(io.BytesIO(zipped.read("python314.zip"))) as stdlib:
            for name in sorted(stdlib.namelist()):
                if name.endswith(".pyc"):
                    relative = relative_path(name[:-1])
                    source = Path(sys.base_prefix) / "Lib" / relative
                    if not source.is_file():
                        raise ValueError(f"Missing standard-library source: {relative}")
                    yield "runtime/Lib/" + relative.as_posix(), source
    system = Path(os.environ["SystemRoot"]) / "System32"
    for name in NATIVE_SUPPORT:
        yield "runtime/" + name, system / name


def platform_version():
    return ".".join(str(value) for value in sys.version_info[:3])


def apply_support(lock_path: Path, archive: Path, output: Path) -> None:
    """Finish an already assembled W1 prototype; immutable payload is checked first.

    This explicit builder operation repairs support/tooling only. It cannot
    overwrite model/dependency payloads or any application/production directory.
    Future assemble runs replay the pinned support hashes from the lock.
    """
    output = output.resolve()
    build = (REPO / "build").resolve()
    if not build.is_relative_to(REPO.resolve()) or not output.is_relative_to(build) or output == build:
        raise ValueError("Support output must be a W1 bundle child of repository build/")
    manifest_path = output / "w1-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["stage"] != "W1 feasibility, NOT application release":
        raise ValueError("Not a W1 prototype")
    entries = {entry["path"]: entry for entry in manifest["files"]}
    print(f"Check immutable W1 payload: {len(entries)} files", flush=True)
    def check(entry):
        path = (output / relative_path(entry["path"])).resolve()
        if not path.is_relative_to(output) or sha256(path) != entry["sha256"]:
            raise ValueError(f"Existing payload drift: {entry['path']}")
    with ThreadPoolExecutor(max_workers=8) as executor:
        for ordinal, _ in enumerate(executor.map(check, entries.values()), 1):
            if ordinal % 5000 == 0:
                print(f"  checked {ordinal}/{len(entries)} files", flush=True)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    pinned = lock.get("support", {}).get("files")
    assets = list(support_files(archive))
    current = [{"path": name, "size": path.stat().st_size, "sha256": sha256(path)} for name, path in assets]
    if pinned is not None and current != pinned:
        raise ValueError("Support donor drift; do not silently recapture reviewed files")
    lock["support"] = {"source": "matching installed CPython sources plus Microsoft-signed installed x64 VC runtime; W1 internal prototype only",
                       "distribution_gate": "W4 must review licensed redistributable source/terms/NOTICE; no public release approval inferred",
                       "files": current}
    dump(lock_path, lock)
    replacements = list(assets)
    replacements += [("validation/probe_w1.py", HERE / "probe_w1.py"),
                     ("validation/验证无驱动CPU.cmd", HERE / "验证无驱动CPU.cmd"),
                     ("locks/w1-lock.json", lock_path)]
    for name, source in replacements:
        target = output / relative_path(name)
        if not target.resolve().is_relative_to(output):
            raise ValueError("Linked support target escaped W1 bundle")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        entries[name] = {"path": name, "size": target.stat().st_size, "sha256": sha256(target)}
    # Source must precede bytecode ZIP for inspect.getsource() to resolve it.
    pth = output / "runtime" / "python314._pth"
    pth.write_text("Lib\npython314.zip\n.\nLib/site-packages\n..\n", encoding="utf-8")
    entries["runtime/python314._pth"] = {"path": "runtime/python314._pth", "size": pth.stat().st_size, "sha256": sha256(pth)}
    manifest["files"] = sorted(entries.values(), key=lambda item: item["path"])
    manifest["bytes"] = sum(item["size"] for item in manifest["files"])
    dump(manifest_path, manifest)
    print(f"SUPPORT VERIFIED: {len(current)} files, {sum(item['size'] for item in current)} bytes", flush=True)


def cached_models():
    from huggingface_hub import snapshot_download
    for role, model in MODELS.items():
        source = Path(snapshot_download(repo_id=model["id"], revision=model["revision"], local_files_only=True))
        yield role, model, source


def capture(lock_path: Path) -> None:
    if sys.version_info[:3] != (3, 14, 0) or sys.platform != "win32":
        raise ValueError("Capture must use the validated Windows CPython 3.14.0 donor")
    roots = roots_from_requirements()
    lock = {"schema": 1, "stage": "W1", "python": PYTHON, "roots": roots,
            "dependency_source": "installed RECORD-verified payload; original wheel URLs/hashes not available",
            "packages": [], "models": {}}
    for name, dist in dependency_closure(roots).items():
        print(f"Freeze {name}=={dist.version}", flush=True)
        if dist.read_text("direct_url.json"):
            raise ValueError(f"Direct/editable dependency needs explicit provenance review: {name}")
        files = [entry for _, _, entry in distribution_files(dist)]
        lock["packages"].append({"name": name, "version": dist.version,
            "requires": dist.requires or [], "wheel": dist.read_text("WHEEL"),
            "license": dist.metadata.get("License-Expression") or dist.metadata.get("License"),
            **fingerprint(files)})
    for role, model, source in cached_models():
        files = [{"path": name, "size": (source / relative_path(name)).stat().st_size,
                  "sha256": sha256(source / relative_path(name))} for name in model["files"]]
        lock["models"][role] = {**{key: model[key] for key in ("id", "revision", "license")}, "files": files}
    dump(lock_path, lock)


def check_output(path: Path) -> Path:
    path = path.resolve()
    build = (REPO / "build").resolve()
    if not build.is_relative_to(REPO.resolve()) or build == REPO.resolve():
        raise ValueError("Repository build/ itself must not escape through a directory link")
    if not path.is_relative_to(build) or path == build:
        raise ValueError("W1 output must be a NEW child directory under this repository's build/")
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing bundle: {path}")
    return path


def assemble(lock_path: Path, archive: Path, output: Path) -> None:
    output = check_output(output)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if lock["python"] != PYTHON or sha256(archive) != PYTHON["sha256"]:
        raise ValueError("Python runtime lock/archive mismatch")
    distributions = dependency_closure(lock["roots"])
    if set(distributions) != {item["name"] for item in lock["packages"]}:
        raise ValueError("Dependency closure changed; review and recapture lock explicitly")
    output.mkdir(parents=True)
    runtime = output / "runtime"
    runtime.mkdir()
    with zipfile.ZipFile(archive) as zipped:
        for item in zipped.infolist():
            relative_path(item.filename)
        zipped.extractall(runtime)
    # No site/user-site/registry/PYTHONPATH resolution, no executable .pth hooks.
    (runtime / "python314._pth").write_text("python314.zip\n.\nLib\nLib/site-packages\n..\n", encoding="utf-8")
    payload = []
    for pinned in lock["packages"]:
        dist = distributions[pinned["name"]]
        if dist.version != pinned["version"]:
            raise ValueError(f"Version drift: {pinned['name']}")
        entries = list(distribution_files(dist))
        actual = fingerprint([entry for _, _, entry in entries])
        if any(actual[key] != pinned[key] for key in actual):
            raise ValueError(f"Installed payload drift: {pinned['name']}")
        print(f"Vendor {pinned['name']} ({actual['bytes']} bytes)", flush=True)
        for ordinal, (relative, source, entry) in enumerate(entries, 1):
            target = runtime / "Lib" / "site-packages" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and sha256(target) != entry["sha256"]:
                raise ValueError(f"Conflicting distribution resources: {entry['path']}")
            shutil.copyfile(source, target)
            if sha256(target) != entry["sha256"]:
                raise ValueError(f"Copy integrity failed: {entry['path']}")
            payload.append({**entry, "path": target.relative_to(output).as_posix()})
            if ordinal % 2000 == 0:
                print(f"  {pinned['name']}: {ordinal}/{len(entries)} resources verified", flush=True)
    for role, _, source in cached_models():
        for entry in lock["models"][role]["files"]:
            target = output / "models" / role / relative_path(entry["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            if sha256(source / relative_path(entry["path"])) != entry["sha256"]:
                raise ValueError(f"Model source drift: {role}/{entry['path']}")
            shutil.copyfile(source / relative_path(entry["path"]), target)
            if sha256(target) != entry["sha256"]:
                raise ValueError(f"Model copy integrity failed: {role}/{entry['path']}")
            payload.append({**entry, "path": target.relative_to(output).as_posix()})
    for name in ("cache", "temp", "reports", "validation", "locks", "licenses"):
        (output / name).mkdir()
    shutil.copyfile(HERE / "probe_w1.py", output / "validation" / "probe_w1.py")
    shutil.copyfile(HERE / "验证无驱动CPU.cmd", output / "validation" / "验证无驱动CPU.cmd")
    shutil.copyfile(lock_path, output / "locks" / "w1-lock.json")
    shutil.copyfile(runtime / "LICENSE.txt", output / "licenses" / "Python-LICENSE.txt")
    dump(output / "licenses" / "inventory.json", {
        "notice": "Third-party license texts are retained inside each installed dist-info/package. Model cards retain their license declarations. Final legal/notices review is W4, not completed by this inventory.",
        "packages": [{"name": item["name"], "version": item["version"], "license": item["license"]} for item in lock["packages"]],
        "models": {role: {key: model[key] for key in ("id", "revision", "license")} for role, model in lock["models"].items()},
    })
    # Cover embedded DLLs/stdlib, lock and probe as well as vendored/model files.
    known = {item["path"] for item in payload}
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.relative_to(output).as_posix() not in known:
            payload.append({"path": path.relative_to(output).as_posix(), "size": path.stat().st_size, "sha256": sha256(path)})
    dump(output / "w1-manifest.json", {"schema": 1, "stage": "W1 feasibility, NOT application release",
         "python": PYTHON, "files": sorted(payload, key=lambda item: item["path"]),
         "bytes": sum(item["size"] for item in payload)})
    if lock.get("support"):
        apply_support(lock_path, archive, output)
    print(f"ASSEMBLED: {output}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["capture", "assemble", "complete"])
    parser.add_argument("--lock", type=Path, default=HERE / "w1-lock.json")
    parser.add_argument("--python-zip", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "capture":
        capture(args.lock)
    else:
        if not args.python_zip or not args.output:
            parser.error("assemble requires --python-zip and --output")
        if args.command == "complete":
            apply_support(args.lock, args.python_zip, args.output)
        else:
            assemble(args.lock, args.python_zip, args.output)


if __name__ == "__main__":
    main()
