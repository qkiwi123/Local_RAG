"""Dynamic socket ownership and tray actions bound to one portable instance."""
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest

from launcher import common, portable, tray


@pytest.fixture
def owned(tmp_path, monkeypatch):
    (tmp_path / "state").mkdir()
    record = {"instance_id": "a" * 32, "root_id": common.root_id(tmp_path), "pid": 123,
              "created_at": 100, "asset_id": "b" * 64, "frontend_build_id": "c" * 64,
              "port": 54321, "device": "cpu"}
    common.write_json(tmp_path, "state/service.json", record)
    monkeypatch.setattr(tray, "same_process", lambda *a, **k: True)
    return tmp_path, record, tray.Controller(tmp_path, record)


def test_bound_socket_stays_reserved():
    with portable.bind_listener(0) as listener:
        port = listener.getsockname()[1]
        with socket.socket() as other:
            with pytest.raises(OSError):
                other.bind(("127.0.0.1", port))


def test_auto_port_falls_back_but_explicit_port_never_does(monkeypatch):
    calls = []
    class Listener:
        def setsockopt(self, *a): pass
        def bind(self, address):
            calls.append(address[1])
            if address[1] != 0: raise OSError("occupied")
        def close(self): calls.append("closed")
    monkeypatch.setattr(portable.socket, "socket", Listener)
    portable.bind_listener(None)
    assert calls == [8000, 0]
    calls.clear()
    with pytest.raises(RuntimeError): portable.bind_listener(8123)
    assert calls == [8123, "closed"]


def test_stop_request_is_instance_bound_and_does_not_force(owned, monkeypatch):
    root, record, controller = owned
    now = [10.0]
    monkeypatch.setattr(tray.time, "monotonic", lambda: now[0])
    assert controller.request_stop()
    assert common.read_json(common.stop_path(root, record)) == {"instance_id": record["instance_id"], "root_id": record["root_id"]}
    now[0] = 39
    assert not controller.force_confirmation_due()
    now[0] = 40
    assert controller.force_confirmation_due()
    text, pending = controller.status()
    assert pending and "正在停止" in text and "54321" in text and "CPU" in text


def test_replacement_instance_is_not_stopped_or_opened(owned, monkeypatch):
    root, record, controller = owned
    common.write_json(root, "state/service.json", {**record, "instance_id": "d" * 32})
    assert not controller.owned() and not controller.request_stop()
    assert not common.stop_path(root, record).exists()
    with pytest.raises(RuntimeError): controller.open_page()
    monkeypatch.setattr(portable, "same_process", lambda *a, **k: pytest.fail("Must not touch replacement PID"))
    assert controller.force_stop()["outcome"] == "instance_changed"


def test_open_page_requires_matching_health_and_uses_actual_port(owned, monkeypatch):
    root, record, controller = owned
    opened = []
    monkeypatch.setattr(tray.webbrowser, "open", opened.append)
    monkeypatch.setattr(tray, "get_health", lambda p: {"portable": {**record, "instance_id": "d" * 32}})
    with pytest.raises(RuntimeError): controller.open_page()
    assert not opened
    monkeypatch.setattr(tray, "get_health", lambda p: {"portable": record})
    controller.open_page()
    assert opened == [portable.browser_url(record)]


def test_repeated_start_reuses_live_tray(owned, monkeypatch):
    root, record, _ = owned
    (root / "logs").mkdir()
    common.write_json(root, "state/tray.json", {**record, "icon_ready": True})
    monkeypatch.setattr(tray.subprocess, "Popen", lambda *a, **k: pytest.fail("Do not duplicate tray"))
    assert tray.ensure(root, record)["outcome"] == "reused"


def test_declining_force_stop_keeps_monitor_and_never_forces(owned, monkeypatch):
    _, _, controller = owned
    controller.requested_at = 0
    monkeypatch.setattr(tray.time, "monotonic", lambda: 31)
    monkeypatch.setattr(controller, "force_stop", lambda: pytest.fail("No force without affirmative answer"))
    native = object.__new__(tray.NativeTray)
    native.controller = controller
    native.hwnd = 1
    native.messages = tray.queue.SimpleQueue()
    native.notify = lambda *a: None
    native.user = SimpleNamespace(MessageBoxW=lambda *a: 7)
    native.tick()
    assert controller.confirmed_once and controller.owned()


@pytest.mark.parametrize("selection", [1001, 1002])
def test_menu_actions_open_or_request_stop_of_bound_instance(owned, monkeypatch, selection):
    import ctypes
    from ctypes import wintypes
    root, record, controller = owned
    opened, items = [], []
    monkeypatch.setattr(controller, "open_page", lambda: opened.append(True))
    native = object.__new__(tray.NativeTray)
    native.c, native.w = ctypes, wintypes
    native.hwnd, native.controller = 1, controller
    native.notify = lambda: None
    native.user = SimpleNamespace(CreatePopupMenu=lambda: 2,
        AppendMenuW=lambda menu, flags, ident, label: items.append((ident, label)),
        GetCursorPos=lambda p: True, SetForegroundWindow=lambda h: True,
        TrackPopupMenuEx=lambda *a: selection, PostMessageW=lambda *a: True, DestroyMenu=lambda h: True)
    native.menu()
    assert (1001, "打开项目网页") in items and (1002, "停止项目并退出") in items
    assert bool(opened) == (selection == 1001)
    assert common.stop_path(root, record).exists() == (selection == 1002)


def test_notify_callback_routes_right_click_and_double_click(owned, monkeypatch):
    events = []
    native = object.__new__(tray.NativeTray)
    native.controller = owned[2]
    native.menu = lambda: events.append("menu")
    monkeypatch.setattr(native.controller, "open_page", lambda: events.append("open"))
    assert native.window_proc(1, 0x8001, 1, 0x205) == 0
    assert native.window_proc(1, 0x8001, 1, 0x203) == 0
    assert events == ["menu", "open"]


def test_tray_failure_does_not_turn_a_running_service_into_start_failure(owned, monkeypatch, capsys):
    monkeypatch.setattr(tray, "ensure", lambda *a: (_ for _ in ()).throw(OSError("desktop unavailable")))
    assert portable.ensure_tray(owned[0], owned[1], False)["outcome"] == "failed"
    assert "停止.cmd" in capsys.readouterr().out
