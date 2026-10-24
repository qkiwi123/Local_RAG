"""W1 assembly guards; actual inference is performed by the bundle probe."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from deploy.windows import prepare_w1 as builder
from deploy.windows import probe_w1 as probe


@pytest.mark.parametrize("value", ["../secret", "x/../../secret", "/absolute", "C:/data", "C:data", "\\\\server\\share", ""])
def test_payload_traversal_rejected(value):
    with pytest.raises(ValueError):
        builder.relative_path(value)


def test_regular_relative_resources():
    assert builder.relative_path("1_Pooling/config.json").as_posix() == "1_Pooling/config.json"
    assert builder.relative_path("torch\\lib\\torch_cuda.dll").as_posix() == "torch/lib/torch_cuda.dll"


def test_assembly_does_not_overwrite_or_write_outside_build(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "REPO", tmp_path)
    with pytest.raises(ValueError):
        builder.check_output(tmp_path)
    with pytest.raises(ValueError):
        builder.check_output(tmp_path / "build")
    existing = tmp_path / "build" / "existing"
    existing.mkdir(parents=True)
    sentinel = existing / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        builder.check_output(existing)
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert builder.check_output(tmp_path / "build" / "new") == tmp_path / "build" / "new"


def test_symlink_output_escape(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "REPO", tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "build").mkdir()
    try:
        (tmp_path / "build" / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("This Windows account cannot create symlinks")
    with pytest.raises(ValueError):
        builder.check_output(tmp_path / "build" / "linked" / "new")


def test_python_archive_mismatch_creates_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "REPO", tmp_path)
    lock = tmp_path / "lock.json"
    lock.write_text(json.dumps({"python": builder.PYTHON}), encoding="utf-8")
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr("python.exe", b"not-python")
    output = tmp_path / "build" / "result"
    with pytest.raises(ValueError, match="runtime lock/archive mismatch"):
        builder.assemble(lock, archive, output)
    assert not output.exists()


def test_support_does_not_write_to_application_or_production_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "REPO", tmp_path)
    with pytest.raises(ValueError, match="W1 bundle child"):
        builder.apply_support(tmp_path / "lock.json", tmp_path / "python.zip", tmp_path / "data")
    assert not (tmp_path / "data").exists()


def test_support_rejects_existing_payload_tampering_before_write(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "REPO", tmp_path)
    root = tmp_path / "build" / "bundle"
    root.mkdir(parents=True)
    resource = root / "resource"
    resource.write_bytes(b"tampered")
    manifest = {"stage": "W1 feasibility, NOT application release", "files": [
        {"path": "resource", "size": 8, "sha256": hashlib.sha256(b"original").hexdigest()}]}
    (root / "w1-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="Existing payload drift"):
        builder.apply_support(tmp_path / "lock.json", tmp_path / "python.zip", root)
    assert resource.read_bytes() == b"tampered"
    assert not (root / "runtime").exists()


def test_model_assets_versions_and_licenses_are_fixed():
    assert len(builder.MODELS) == 2
    assert builder.MODELS["embedding"]["license"] == "MIT"
    assert builder.MODELS["reranker"]["license"] == "Apache-2.0"
    assert all(len(model["revision"]) == 40 for model in builder.MODELS.values())
    assert "pytorch_model.bin" in builder.MODELS["embedding"]["files"]
    assert "model.safetensors" in builder.MODELS["reranker"]["files"]
    assert not any(name.startswith("assets/") for model in builder.MODELS.values() for name in model["files"])


def test_closure_uses_activated_extras_and_rejects_version_drift(monkeypatch):
    class Distribution:
        version = "1.0"

        def __init__(self, requires):
            self.requires = requires

    installed = {"parent": Distribution(['base>=1', 'extra-child>=1; extra == "fast"']),
                 "base": Distribution([]), "extra-child": Distribution([])}
    monkeypatch.setattr(builder.metadata, "distribution", installed.__getitem__)
    assert set(builder.dependency_closure(["parent==1.0"])) == {"parent", "base"}
    assert set(builder.dependency_closure(["parent[fast]==1.0"])) == set(installed)
    with pytest.raises(ValueError, match="Incompatible"):
        builder.dependency_closure(["parent==2.0"])


def test_probe_rejects_corrupt_or_escaping_payload(tmp_path):
    file = tmp_path / "resource"
    file.write_bytes(b"original")
    entry = {"path": "resource", "size": 8, "sha256": hashlib.sha256(b"original").hexdigest()}
    manifest = tmp_path / "w1-manifest.json"
    manifest.write_text(json.dumps({"files": [entry], "bytes": 8}), encoding="utf-8")
    assert probe.verify_payload(tmp_path)["sha256_verified"]
    file.write_bytes(b"modified")
    with pytest.raises(ValueError, match="hash mismatch"):
        probe.verify_payload(tmp_path)
    entry["path"] = "../outside"
    manifest.write_text(json.dumps({"files": [entry], "bytes": 8}), encoding="utf-8")
    with pytest.raises(ValueError, match="escaping"):
        probe.verify_payload(tmp_path)


@pytest.mark.parametrize("action", ["network", "write"])
def test_real_audit_guard_blocks_network_and_external_write(tmp_path, action):
    # Audit hooks cannot be removed, so execute the guard in a disposable child.
    root = tmp_path / "bundle"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    code = (
        "from pathlib import Path; import socket; "
        "from deploy.windows.probe_w1 import audit_guard; "
        f"audit_guard(Path({str(root)!r}), []); "
        + ("socket.create_connection(('127.0.0.1', 9))" if action == "network" else f"Path({str(outside)!r}).write_text('bad')")
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=builder.REPO, capture_output=True, text=True)
    assert result.returncode != 0
    assert "PermissionError" in result.stderr
    assert not outside.exists()


def test_guard_allows_only_os_null_device_not_arbitrary_external_file(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    code = (
        "from pathlib import Path; import os; "
        "from deploy.windows.probe_w1 import audit_guard; "
        f"audit_guard(Path({str(root)!r}), []); "
        "file = open(os.devnull, 'r+b'); file.close()"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=builder.REPO, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_probe_refuses_any_import_path_outside_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "path", [str(tmp_path / "global")])
    with pytest.raises(ValueError, match="Global import path"):
        probe.assert_module_paths(tmp_path / "bundle")


def test_native_runtime_uses_current_unicode_loader_path(tmp_path, monkeypatch):
    root = tmp_path / "中文 空格 验证包"
    runtime = root / "runtime"
    runtime.mkdir(parents=True)
    library = runtime / "MSVCP140.dll"
    library.write_bytes(b"fixture")
    monkeypatch.setattr(probe, "loaded_module_paths", lambda: [library.resolve()])
    assert probe.native_runtime_paths(root.resolve()) == {"MSVCP140.dll": "runtime/MSVCP140.dll"}


def test_native_runtime_still_rejects_external_or_missing_library(tmp_path, monkeypatch):
    root = tmp_path / "bundle"
    root.mkdir()
    library = tmp_path / "msvcp140.dll"
    library.write_bytes(b"fixture")
    monkeypatch.setattr(probe, "loaded_module_paths", lambda: [library.resolve()])
    with pytest.raises(ValueError, match="outside the bundle"):
        probe.native_runtime_paths(root)
    monkeypatch.setattr(probe, "loaded_module_paths", lambda: [])
    with pytest.raises(ValueError, match="No loaded MSVC"):
        probe.native_runtime_paths(root)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows loader API")
def test_actual_windows_loader_enumeration_contains_current_executable():
    # A Windows venv executable redirects to its base interpreter image.
    expected = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    assert expected in probe.loaded_module_paths()


def test_bm25_probe_query_survives_persistence_and_actual_mmap(tmp_path):
    import bm25s
    import numpy as np
    corpus = bm25s.tokenize(["知识库 备份 复制 完整 目录", "苹果树 浇水", "配置 模型 密钥"], stopwords=[], show_progress=False)
    model = bm25s.BM25(auto_compile=False)
    model.index(corpus, show_progress=False)
    model.save(tmp_path / "bm25", show_progress=False)
    loaded = bm25s.BM25.load(tmp_path / "bm25", mmap=True, load_corpus=False, show_progress=False, auto_compile=False)
    try:
        assert all(isinstance(loaded.scores[key], np.memmap) for key in ("data", "indices", "indptr"))
        results, _ = loaded.retrieve(bm25s.tokenize(["知识库 备份"], stopwords=[], show_progress=False), k=1, show_progress=False)
        assert int(results[0][0]) == 0
    finally:
        for key in ("data", "indices", "indptr"):
            loaded.scores[key]._mmap.close()
