"""W3 ownership, startup fallback, maintenance and portable-only HTTP guards."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace

import httpx
import pytest

from launcher import common, portable


@pytest.fixture(autouse=True)
def no_desktop_tray_in_unit_tests(monkeypatch):
    monkeypatch.setattr(portable, "ensure_tray", lambda *a: {"outcome": "test"})


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "W3 中文 空格"
    root.mkdir()
    for name in ("state", "logs", "cache", "temp", "data", "models"):
        (root / name).mkdir()
    entries = []
    for relative in ("runtime/python.exe", "runtime/python314._pth", "backend/main.py",
                     "launcher/portable.py", "web/dist/index.html", "locks/w1-lock.json"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
        entries.append({"path": relative, "size": 7, "sha256": hashlib.sha256(b"fixture").hexdigest()})
    manifest = {"format": 1, "stage": "W3 internal prototype", "files": entries,
                "frontend_build_id": hashlib.sha256(b"fixture").hexdigest()}
    (root / "portable-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def record(root):
    return {"instance_id": "a" * 32, "root_id": common.root_id(root), "pid": 123456,
            "created_at": 100.0, "asset_id": "b" * 64, "frontend_build_id": "c" * 64,
            "port": 18080, "device": "cuda:0", "batch_profile": "normal"}


@pytest.mark.parametrize("relative", ["../outside", "C:/outside", "C:outside", "\\\\server\\file"])
def test_no_path_escape(bundle, relative):
    with pytest.raises(ValueError):
        common.safe_path(bundle, relative)


@pytest.mark.parametrize("value", ["../file", "x" * 32, "a" * 31, "a" * 33, ""])
def test_instance_ids_are_not_paths(value):
    with pytest.raises(ValueError):
        common.valid_instance(value)


def test_asset_cache_rechecks_modified_same_size_file(bundle):
    common.validate_assets(bundle)
    path = bundle / "backend/main.py"
    old = path.stat()
    path.write_bytes(b"changed")
    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns + 10000000))
    with pytest.raises(ValueError, match="资源校验失败"):
        common.validate_assets(bundle)


def test_missing_frontend_refuses_start_before_backend(bundle, monkeypatch):
    (bundle / "web/dist/index.html").unlink()
    monkeypatch.setattr(portable, "inspect_gpu", lambda *a: pytest.fail("No GPU import before asset validation"))
    with pytest.raises(OSError):
        portable.start(bundle, no_browser=True)


@pytest.mark.parametrize("difference, expected", [(-100.0, False), (100.0, False), (0.0, True)])
def test_process_creation_time_is_checked_in_both_directions(bundle, monkeypatch, difference, expected):
    import psutil

    saved = record(bundle)
    fake = SimpleNamespace(create_time=lambda: 100 + difference,
                           exe=lambda: str(bundle / "runtime/python.exe"),
                           cmdline=lambda: [str(bundle / "runtime/python.exe"), str(bundle / "launcher/portable.py"),
                                            "serve", "--instance-id", saved["instance_id"]])
    monkeypatch.setattr(psutil, "Process", lambda _: fake)
    assert common.same_process(bundle, saved) is expected


def test_reused_pid_with_foreign_command_is_not_our_process(bundle, monkeypatch):
    import psutil

    fake = SimpleNamespace(create_time=lambda: 100.0,
                           exe=lambda: str(bundle / "runtime/python.exe"), cmdline=lambda: ["python", "other.py"])
    monkeypatch.setattr(psutil, "Process", lambda _: fake)
    assert not common.same_process(bundle, record(bundle))


def test_http_200_is_not_instance_ownership(bundle):
    saved = record(bundle)
    assert not common.matches_health(bundle, saved, {"status": "ok"})
    assert not common.matches_health(bundle, saved, {"portable": {**saved, "instance_id": "d" * 32}})
    assert common.matches_health(bundle, saved, {"portable": saved})


def test_other_directory_is_not_same_instance(bundle, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    saved = record(bundle)
    assert not common.matches_health(other, saved, {"portable": saved})


def test_stop_does_not_signal_mismatched_process(bundle, monkeypatch):
    saved = record(bundle)
    common.write_json(bundle, "state/service.json", saved)
    monkeypatch.setattr(portable, "same_process", lambda *a, **k: False)
    monkeypatch.setattr(portable, "service_locked", lambda _: False)
    assert portable.stop(bundle, timeout=0, force=True)["outcome"] == "not_running"
    assert not common.stop_path(bundle, saved).exists()


def test_force_stop_rechecks_identity_immediately_before_termination(bundle, monkeypatch):
    import psutil

    common.write_json(bundle, "state/service.json", record(bundle))
    answers = iter([True, True, True, False])
    monkeypatch.setattr(portable, "same_process", lambda *a, **k: next(answers))
    monkeypatch.setattr(psutil, "Process", lambda _: pytest.fail("Changed PID must not be terminated"))
    with pytest.raises(RuntimeError, match="身份已变化"):
        portable.stop(bundle, timeout=0, force=True)


def test_failed_start_uses_only_its_retained_child_handle(bundle):
    calls = []
    class Child:
        def wait(self, timeout):
            calls.append(("wait", timeout))
            if len(calls) == 1:
                raise subprocess.TimeoutExpired("own-child", timeout)
        def kill(self):
            calls.append(("kill-own-handle",))
    portable._cleanup_child(bundle, Child(), "a" * 32, force=True)
    assert calls == [("wait", 15), ("kill-own-handle",), ("wait", 10)]


def test_cancelled_start_does_not_implicitly_force_kill(bundle):
    class Child:
        def wait(self, timeout):
            raise subprocess.TimeoutExpired("own-child", timeout)
        def kill(self):
            pytest.fail("User cancellation is not force-stop confirmation")
    with pytest.raises(RuntimeError, match="等待任务完成"):
        portable._cleanup_child(bundle, Child(), "a" * 32, force=False)


def test_corrupt_record_does_not_skip_failed_start_child_cleanup(bundle):
    (bundle / "state/service.json").write_text("broken-json", encoding="utf-8")
    killed = []
    class Child:
        def wait(self, timeout):
            if not killed:
                raise subprocess.TimeoutExpired("own-child", timeout)
        def kill(self):
            killed.append(True)
    portable._cleanup_child(bundle, Child(), "a" * 32, force=True)
    assert killed == [True]
    assert (bundle / "state/service.json").read_text(encoding="utf-8") == "broken-json"


def test_forget_record_preserves_a_replacement_instance(bundle):
    old = record(bundle)
    common.write_json(bundle, "state/service.json", {**old, "instance_id": "e" * 32})
    common.forget_record(bundle, old)
    assert common.read_record(bundle)["instance_id"] == "e" * 32


def test_port_conflict_does_not_adopt_or_kill_listener():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        with pytest.raises(RuntimeError, match="已占用"):
            portable.available_port(listener.getsockname()[1])
        assert listener.fileno() >= 0


def _mock_start(bundle, monkeypatch):
    import backend.portable

    manifest, asset_id = common.load_assets(bundle)
    monkeypatch.setattr(portable, "validate_assets", lambda _: (manifest, asset_id))
    monkeypatch.setattr(portable, "available_port", lambda _: None)
    monkeypatch.setattr(backend.portable, "prepare_environment", lambda *a, **k: {})
    calls = []
    def attempt(*args, **kwargs):
        calls.append(kwargs)
        return {**record(bundle), "device": kwargs["device"], "batch_profile": kwargs["batch_profile"]}
    return calls, attempt


def test_cpu_fallback_requires_confirmation(bundle, monkeypatch):
    _mock_start(bundle, monkeypatch)
    monkeypatch.setattr(portable, "inspect_gpu", lambda *a: {"available": False, "kind": "gpu_unavailable"})
    monkeypatch.setattr(portable, "confirm_cpu", lambda *a: False)
    monkeypatch.setattr(portable, "_start_attempt", lambda *a, **k: pytest.fail("No implicit CPU startup"))
    with pytest.raises(portable.StartCancelled):
        portable.start(bundle, no_browser=True)


def test_explicit_cpu_does_not_probe_gpu(bundle, monkeypatch):
    calls, attempt = _mock_start(bundle, monkeypatch)
    monkeypatch.setattr(portable, "inspect_gpu", lambda *a: pytest.fail("Explicit CPU does not probe CUDA"))
    monkeypatch.setattr(portable, "_start_attempt", attempt)
    result = portable.start(bundle, cpu=True, no_browser=True)
    assert result["device"] == "cpu" and len(calls) == 1


def test_broken_runtime_is_not_disguised_as_gpu_fallback(bundle, monkeypatch):
    _mock_start(bundle, monkeypatch)
    monkeypatch.setattr(portable, "inspect_gpu", lambda *a: {"available": False, "kind": "runtime_error", "error": "bad DLL"})
    monkeypatch.setattr(portable, "confirm_cpu", lambda *a: pytest.fail("Broken assets are not a GPU choice"))
    with pytest.raises(RuntimeError, match="不能当作GPU"):
        portable.start(bundle, no_browser=True)


def test_oom_retries_only_batch_once_then_confirmed_cpu(bundle, monkeypatch):
    calls, final_attempt = _mock_start(bundle, monkeypatch)
    monkeypatch.setattr(portable, "inspect_gpu", lambda *a: {"available": True})
    monkeypatch.setattr(portable, "confirm_cpu", lambda *a: True)
    def attempt(*args, **kwargs):
        if kwargs["device"] == "cuda:0":
            calls.append(kwargs)
            raise RuntimeError('MODEL_WARMUP:{"error_type":"OutOfMemoryError"}')
        return final_attempt(*args, **kwargs)
    monkeypatch.setattr(portable, "_start_attempt", attempt)
    assert portable.start(bundle, no_browser=True)["device"] == "cpu"
    assert [(item["device"], item["batch_profile"]) for item in calls] == [
        ("cuda:0", "normal"), ("cuda:0", "reduced"), ("cpu", "normal")]


def test_start_reuses_only_current_ready_instance(bundle, monkeypatch):
    saved = record(bundle)
    manifest, asset_id = common.load_assets(bundle)
    saved["asset_id"] = asset_id
    common.write_json(bundle, "state/service.json", saved)
    monkeypatch.setattr(portable, "same_process", lambda *a, **k: True)
    monkeypatch.setattr(portable, "validate_assets", lambda _: (manifest, asset_id))
    monkeypatch.setattr(portable, "get_health", lambda _: {"portable": saved, "model_warmup": {"status": "ready"}})
    monkeypatch.setattr(portable, "_start_attempt", lambda *a, **k: pytest.fail("Duplicate backend"))
    assert portable.start(bundle, no_browser=True)["outcome"] == "reused"
    assert "/settings?build=" in portable.browser_url(saved)


def test_default_browser_opens_settings_only_after_success(bundle, monkeypatch):
    _, attempt = _mock_start(bundle, monkeypatch)
    opened = []
    monkeypatch.setattr(portable, "_start_attempt", attempt)
    monkeypatch.setattr(portable.webbrowser, "open", opened.append)
    portable.start(bundle, cpu=True)
    assert len(opened) == 1 and "/settings?build=" in opened[0]


def test_maintenance_deletes_only_listed_rebuildable_files(bundle):
    paths = ("data/keep.txt", "models/keep.txt", "logs/keep.log", "temp/unknown.txt", "cache/unknown.txt")
    for relative in (*paths, "temp/jieba.cache", "state/verified-assets.json"):
        (bundle / relative).write_text("keep", encoding="utf-8")
    result = portable.maintain(bundle, "cache", yes=True)
    assert set(result["files"]) == {"temp/jieba.cache", "state/verified-assets.json"}
    assert all((bundle / relative).is_file() for relative in paths)


def test_maintenance_refuses_running_service(bundle, monkeypatch):
    path = bundle / "temp/jieba.cache"
    path.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(portable, "service_locked", lambda _: True)
    with pytest.raises(RuntimeError, match="请先停止"):
        portable.maintain(bundle, "cache", yes=True)
    assert path.read_text(encoding="utf-8") == "keep"


def test_maintenance_refuses_links_even_if_target_is_inside_root(bundle):
    (bundle / "temp").rmdir()
    try:
        (bundle / "temp").symlink_to(bundle / "data", target_is_directory=True)
    except OSError:
        pytest.skip("Windows account cannot create symlinks")
    with pytest.raises(ValueError, match="链接"):
        portable.maintain(bundle, "cache", yes=True)


@pytest.mark.parametrize("method, host, origin, fetch_site, status", [
    ("GET", "localhost:8000", None, None, 200),
    ("POST", "127.0.0.1:8000", "http://127.0.0.1:8000", "same-origin", 200),
    ("POST", "127.0.0.1:8000", None, None, 200),
    ("POST", "localhost:8000", "http://evil.example", "cross-site", 403),
    ("POST", "localhost:8000", None, "cross-site", 403),
    ("POST", "localhost:8000", "null", None, 403),
    ("POST", "localhost:8000", "http://localhost:8001", "same-site", 403),
    ("POST", "localhost:8000", "https://localhost:8000", None, 403),
    ("GET", "evil.example:8000", None, None, 403),
    ("GET", "localhost.evil:8000", None, None, 403),
    ("GET", "user@localhost:8000", None, None, 403),
    ("GET", "localhost:9999", None, None, 403),
])
def test_portable_host_origin_and_simple_cross_site_write(method, host, origin, fetch_site, status):
    from backend.services.portable_runtime import LocalOnlyMiddleware

    async def application(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})
    async def run():
        headers = {"host": host, "content-type": "text/plain"}
        if origin is not None:
            headers["origin"] = origin
        if fetch_site is not None:
            headers["sec-fetch-site"] = fetch_site
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=LocalOnlyMiddleware(application, lambda: 8000)),
                                     base_url="http://localhost:8000") as client:
            response = await client.request(method, "/", headers=headers)
            assert response.status_code == status
    asyncio.run(run())
