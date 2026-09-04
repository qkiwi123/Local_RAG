"""Audited, single-configuration v3.1 GPU baseline (no parameter search).

Run from the repository root with .venv-gpu/Scripts/python.exe -B -u.
The original dataset builder and baseline entry point remain unchanged.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
sys.path.insert(0, str(PROJECT))

from eval.crud_rag_1000.v3_1 import run_retrieval_baseline as runner


# CLI pins the other retrieval/model/chunking parameters. These were implicit
# defaults in the old runner; pin their IDENTICAL values for reproducibility.
PINNED_ENV = {
    "DENSE_CANDIDATE_K": "15",
    "MAX_CHUNKS_PER_DOCUMENT": "3",
    "RERANKER_MAX_LENGTH": "512",
    "RERANKER_ENABLED": "true",
    "RERANKER_CANDIDATE_K": "15",
    "LLM_ENABLED": "false",
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_DISABLE_PROGRESS_BARS": "1",
    "ANONYMIZED_TELEMETRY": "False",
    "DO_NOT_TRACK": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PREWARM_MODELS": "false",
}


def checked_paths(label: str) -> tuple[Path, Path]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", label):
        raise ValueError("Unsafe label")
    paths = (ROOT / "runs" / label, ROOT / "baseline_audits" / label)
    for path in paths:
        for ancestor in [path, *path.parents]:
            if ancestor.is_symlink() or ancestor.is_junction():
                raise ValueError(f"Linked output path: {ancestor}")
        if not path.resolve().is_relative_to(ROOT):
            raise ValueError("Output escaped v3_1")
        if path.exists():
            raise FileExistsError(f"Use a NEW label; existing output: {path}")
    return paths


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def snapshot(paths: list[Path]) -> dict:
    files = set()
    for path in paths:
        if path.is_symlink() or path.is_junction():
            raise ValueError(f"Refuse linked input: {path}")
        if path.is_file():
            files.add(path)
        elif path.is_dir():
            for current, directories, names in os.walk(path, followlinks=False):
                for name in [*directories, *names]:
                    child = Path(current) / name
                    if child.is_symlink() or child.is_junction():
                        raise ValueError(f"Refuse linked input: {child}")
                files.update(Path(current) / name for name in names)
    return {
        path.relative_to(PROJECT).as_posix(): {
            "sha256": digest(path), "bytes": path.stat().st_size
        }
        for path in sorted(files)
    }


def write_json(path: Path, payload: dict) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def gpu_sample() -> dict:
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.used,"
         "utilization.gpu,power.draw", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=10, check=True,
    )
    return {"timestamp": datetime.now().astimezone().isoformat(),
            "columns": ["name", "driver_version", "total_mib", "used_mib",
                        "utilization_percent", "power_w"],
            "devices": [line.strip().split(", ") for line in result.stdout.splitlines()]}


def read_saved_retrieval() -> dict:
    """Read only the non-sensitive retrieval override, without bootstrapping DB."""
    path = PROJECT / "data/metadata.db"
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        exists = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='runtime_settings'"
        ).fetchone()
        row = connection.execute(
            "SELECT revision,payload_json FROM runtime_settings WHERE singleton_id=1"
        ).fetchone() if exists else None
        return {"read_only": True, "revision": row[0] if row else None,
                "retrieval_override": json.loads(row[1]).get("retrieval", {}) if row else {}}
    finally:
        connection.close()


class Tee:
    def __init__(self, terminal, log):
        self.terminal, self.log = terminal, log

    def write(self, value):
        self.terminal.write(value)
        self.log.write(value)
        self.log.flush()
        return len(value)

    def flush(self):
        self.terminal.flush()
        self.log.flush()

    def isatty(self):
        return False


def execute(label: str) -> int:
    run_dir, audit_dir = checked_paths(label)
    # Never import backend.config until the existing CLI has set isolated paths.
    if any(name.startswith("backend.") for name in sys.modules):
        raise RuntimeError("Backend was imported before isolation")
    protected_paths = [PROJECT / ".env", PROJECT / "data/metadata.db",
                       PROJECT / "data/metadata.db-wal", PROJECT / "data/metadata.db-shm",
                       PROJECT / "data/chroma_db", PROJECT / "data/uploads"]
    input_paths = [ROOT / "questions_v3_120q.json", ROOT / "id_mapping.json",
                   ROOT.parent / "corpus", ROOT.parent / "corpus_v3_manifest.json"]
    code_paths = [*PROJECT.glob("backend/**/*.py"), *PROJECT.glob("eval/*.py"),
                  ROOT / "execute_frozen_baseline.py", ROOT / "run_retrieval_baseline.py",
                  ROOT.parent / "v3/run_retrieval_baseline.py"]
    before = {"production": snapshot(protected_paths), "inputs": snapshot(input_paths),
              "code": snapshot(code_paths)}
    saved = read_saved_retrieval()
    if saved["retrieval_override"]:
        raise RuntimeError("New UI retrieval overrides found; review before running")
    initial_gpu = gpu_sample()
    os.environ.update(PINNED_ENV)
    sys.dont_write_bytecode = True
    audit_dir.mkdir(parents=True, exist_ok=False)
    temp_dir = audit_dir / "temp"
    temp_dir.mkdir()
    os.environ.update({"TEMP": str(temp_dir), "TMP": str(temp_dir)})
    tempfile.tempdir = str(temp_dir)
    write_json(audit_dir / "preflight.json", {
        "started_at": datetime.now().astimezone().isoformat(),
        "python": sys.executable, "python_version": sys.version,
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT, text=True).strip(),
        "git_status": subprocess.check_output(["git", "status", "--short"], cwd=PROJECT, text=True),
        "packages": {name: version(name) for name in
                     ["torch", "transformers", "sentence-transformers", "chromadb", "numpy"]},
        "argv": runner.build_eval_argv(label=label, profile="gpu"),
        "explicit_environment": PINNED_ENV,
        "run_directory": str(run_dir), "initial_gpu": initial_gpu,
        "saved_runtime_settings": saved,
        "snapshots": before,
        "scope": "retrieval_only; fixed k15; all 120 draft questions; no tuning",
        "settings_source": "CLI and pinned environment; eval adapter does not load UI snapshots",
    })
    blocked = []

    def deny_network(event, args):
        if event == "socket.connect" and args[0].family in (socket.AF_INET, socket.AF_INET6):
            blocked.append({"event": event, "at_s": round(time.perf_counter() - start, 3)})
            raise PermissionError("This retrieval baseline forbids outbound IP connections")

    stop = threading.Event()

    def monitor():
        with (audit_dir / "gpu-samples.jsonl").open("x", encoding="utf-8") as output:
            while not stop.is_set():
                try:
                    sample = gpu_sample()
                except Exception as exc:
                    sample = {"error": type(exc).__name__}
                output.write(json.dumps(sample, ensure_ascii=False) + "\n")
                output.flush()
                stop.wait(5)

    start = time.perf_counter()
    sys.addaudithook(deny_network)
    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    success = False
    try:
        with (audit_dir / "execution.log").open("x", encoding="utf-8") as log:
            with redirect_stdout(Tee(sys.stdout, log)), redirect_stderr(Tee(sys.stderr, log)):
                runner.main(["--profile", "gpu", "--label", label])
        success = True
    finally:
        stop.set()
        thread.join(timeout=12)
        after = {"production": snapshot(protected_paths), "inputs": snapshot(input_paths),
                 "code": snapshot(code_paths)}
        unchanged = {key: before[key] == after[key] for key in before}
        modules = {name: str(Path(module.__file__).resolve())
                   for name, module in sorted(sys.modules.items())
                   if name.startswith(("backend.services.", "eval."))
                   and getattr(module, "__file__", None)}
        write_json(audit_dir / "completion.json", {
            "completed_at": datetime.now().astimezone().isoformat(),
            "runner_completed": success, "wall_s": round(time.perf_counter() - start, 3),
            "unchanged": unchanged, "snapshots_after": after,
            "llm_module_imported": "backend.services.llm" in sys.modules,
            "outbound_ip_connections_denied": blocked,
            "network_policy": "Python audit hook rejects every socket.connect for IPv4/IPv6",
            "actual_modules": modules,
        })
        if not all(unchanged.values()):
            raise RuntimeError("Inputs/code/production changed; inspect completion.json")
        if "backend.services.llm" in sys.modules:
            raise RuntimeError("Unexpected generation module import; inspect execution")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run_dir, audit_dir = checked_paths(args.label)
    if args.dry_run:
        print(json.dumps({"run_directory": str(run_dir), "audit_directory": str(audit_dir),
                          "environment": PINNED_ENV,
                          "argv": runner.build_eval_argv(label=args.label, profile="gpu")},
                         ensure_ascii=False, indent=2))
        return 0
    return execute(args.label)


if __name__ == "__main__":
    raise SystemExit(main())
