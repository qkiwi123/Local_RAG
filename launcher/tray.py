"""Native Windows notification-area entry; no model imports or extra packages."""
from __future__ import annotations

from contextlib import suppress
import logging
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import webbrowser

from launcher.common import (file_lock, get_health, matches_health, read_json, read_record,
                            safe_path, same_process, stop_path, write_json)

logger = logging.getLogger(__name__)


class Controller:
    def __init__(self, root: Path, record: dict):
        self.root, self.record = root, record
        self.requested_at = None
        self.confirmed_once = False

    def owned(self) -> bool:
        try:
            current = read_record(self.root)
            return bool(current and all(current.get(k) == self.record.get(k) for k in
                        ("instance_id", "pid", "created_at", "asset_id")) and same_process(self.root, self.record))
        except (OSError, ValueError, KeyError):
            return False

    def status(self) -> tuple[str, bool]:
        mode = "CPU" if self.record["device"] == "cpu" else "GPU"
        pending = stop_path(self.root, self.record).exists()
        return f"本地 RAG · {'正在停止' if pending else '运行中'} · {mode} · 127.0.0.1:{self.record['port']}", pending

    def open_page(self):
        if not self.owned() or not matches_health(self.root, self.record, get_health(self.record["port"])):
            raise RuntimeError("当前实例正在忙碌或已变化，请稍后重试")
        from launcher.portable import browser_url
        webbrowser.open(browser_url(self.record))

    def request_stop(self):
        if not self.owned():
            return False
        write_json(self.root, f"state/stop-{self.record['instance_id']}.json",
                   {"instance_id": self.record["instance_id"], "root_id": self.record["root_id"]})
        self.requested_at = time.monotonic() if self.requested_at is None else time.monotonic() - 31
        self.confirmed_once = False
        return True

    def force_confirmation_due(self) -> bool:
        return (self.requested_at is not None and not self.confirmed_once
                and time.monotonic() - self.requested_at >= 30)

    def force_stop(self):
        from launcher.portable import stop
        return stop(self.root, timeout=0, force=True, expected_instance_id=self.record["instance_id"])


def ensure(root: Path, record: dict) -> dict:
    """Called under start's command lock; wait for Shell_NotifyIcon success."""
    path = safe_path(root, "state/tray.json")
    deadline = time.monotonic() + 6
    while path.exists():
        try:
            old = read_json(path)
            if not same_process(root, old, action="tray"):
                break
            if old.get("instance_id") == record["instance_id"] and old.get("icon_ready"):
                return {"outcome": "reused", "pid": old["pid"]}
        except (OSError, ValueError, KeyError):
            break
        if time.monotonic() >= deadline:
            return {"outcome": "failed"}
        time.sleep(0.1)
    with safe_path(root, "logs/tray.log").open("ab") as log:
        child = subprocess.Popen([str(root / "runtime/python.exe"), str(root / "launcher/portable.py"),
                                  "tray", "--instance-id", record["instance_id"]], cwd=root,
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and child.poll() is None:
        with suppress(OSError, ValueError, KeyError):
            saved = read_json(path)
            if saved["pid"] == child.pid and saved.get("icon_ready") and same_process(root, saved, action="tray"):
                return {"outcome": "started", "pid": child.pid}
        time.sleep(0.1)
    return {"outcome": "failed"}


class NativeTray:
    def __init__(self, controller: Controller):
        import ctypes as c
        from ctypes import wintypes as w
        self.c, self.w, self.controller = c, w, controller
        self.user = c.WinDLL("user32", use_last_error=True)
        self.shell = c.WinDLL("shell32", use_last_error=True)
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.callback_type = c.WINFUNCTYPE(c.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
        callback_type = self.callback_type
        class WindowClass(c.Structure):
            _fields_ = [("style", w.UINT), ("proc", callback_type), ("class_extra", c.c_int),
                        ("window_extra", c.c_int), ("instance", w.HINSTANCE), ("icon", w.HICON),
                        ("cursor", w.HANDLE), ("background", w.HBRUSH), ("menu", w.LPCWSTR), ("name", w.LPCWSTR)]
        class Notify(c.Structure):
            _fields_ = [("size", w.DWORD), ("window", w.HWND), ("id", w.UINT), ("flags", w.UINT),
                        ("message", w.UINT), ("icon", w.HICON), ("tip", w.WCHAR * 128),
                        ("state", w.DWORD), ("state_mask", w.DWORD), ("info", w.WCHAR * 256),
                        ("version", w.UINT), ("title", w.WCHAR * 64), ("info_flags", w.DWORD),
                        ("guid", c.c_byte * 16), ("balloon_icon", w.HICON)]
        self.WindowClass, self.Notify = WindowClass, Notify
        def signature(dll, name, result, args):
            fn = getattr(dll, name); fn.restype = result; fn.argtypes = args
        signature(self.kernel, "GetModuleHandleW", w.HMODULE, [w.LPCWSTR])
        signature(self.user, "RegisterClassW", w.ATOM, [c.POINTER(WindowClass)])
        signature(self.user, "CreateWindowExW", w.HWND, [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
                  c.c_int, c.c_int, c.c_int, c.c_int, w.HWND, w.HMENU, w.HINSTANCE, c.c_void_p])
        signature(self.user, "DefWindowProcW", c.c_ssize_t, [w.HWND, w.UINT, w.WPARAM, w.LPARAM])
        signature(self.user, "SetTimer", c.c_size_t, [w.HWND, c.c_size_t, w.UINT, c.c_void_p])
        signature(self.user, "KillTimer", w.BOOL, [w.HWND, c.c_size_t])
        signature(self.user, "DestroyWindow", w.BOOL, [w.HWND])
        signature(self.user, "IsWindow", w.BOOL, [w.HWND])
        signature(self.user, "UnregisterClassW", w.BOOL, [w.LPCWSTR, w.HINSTANCE])
        signature(self.user, "GetMessageW", w.BOOL, [c.POINTER(w.MSG), w.HWND, w.UINT, w.UINT])
        signature(self.user, "TranslateMessage", w.BOOL, [c.POINTER(w.MSG)])
        signature(self.user, "DispatchMessageW", c.c_ssize_t, [c.POINTER(w.MSG)])
        signature(self.user, "PostQuitMessage", None, [c.c_int])
        signature(self.user, "RegisterWindowMessageW", w.UINT, [w.LPCWSTR])
        signature(self.user, "CreatePopupMenu", w.HMENU, [])
        signature(self.user, "AppendMenuW", w.BOOL, [w.HMENU, w.UINT, c.c_size_t, w.LPCWSTR])
        signature(self.user, "DestroyMenu", w.BOOL, [w.HMENU])
        signature(self.user, "GetCursorPos", w.BOOL, [c.POINTER(w.POINT)])
        signature(self.user, "SetForegroundWindow", w.BOOL, [w.HWND])
        signature(self.user, "TrackPopupMenuEx", w.UINT, [w.HMENU, w.UINT, c.c_int, c.c_int, w.HWND, c.c_void_p])
        signature(self.user, "PostMessageW", w.BOOL, [w.HWND, w.UINT, w.WPARAM, w.LPARAM])
        signature(self.user, "MessageBoxW", c.c_int, [w.HWND, w.LPCWSTR, w.LPCWSTR, w.UINT])
        signature(self.user, "CreateIcon", w.HICON, [w.HINSTANCE, c.c_int, c.c_int, c.c_byte, c.c_byte, c.c_void_p, c.c_void_p])
        signature(self.user, "DestroyIcon", w.BOOL, [w.HICON])
        signature(self.shell, "Shell_NotifyIconW", w.BOOL, [w.DWORD, c.POINTER(Notify)])
        self.instance = self.kernel.GetModuleHandleW(None)
        self.class_name = f"LocalRAGTray-{os.getpid()}"
        self.callback = self.callback_type(self.window_proc)
        self.hwnd = None
        self.icons = []
        self.messages = queue.SimpleQueue()
        self.taskbar_created = self.user.RegisterWindowMessageW("TaskbarCreated")

    def make_icon(self, pending):
        c = self.c
        pixels = bytearray(32 * 32 * 4)
        mask = bytearray(b"\xff" * 128)
        for y in range(32):
            for x in range(32):
                if (x - 15.5) ** 2 + (y - 15.5) ** 2 > 15 ** 2:
                    continue
                mask[y * 4 + x // 8] &= ~(128 >> (x % 8))
                white = (9 <= x <= 11 and 8 <= y <= 24) or (9 <= x <= 20 and y in {8, 9, 16, 17}) or (19 <= x <= 21 and 9 <= y <= 17) or (15 <= y <= 24 and x in {y - 2, y - 1})
                color = (255, 255, 255, 255) if white else ((40, 160, 230, 255) if pending else (230, 115, 35, 255))
                pixels[(y * 32 + x) * 4:(y * 32 + x + 1) * 4] = bytes(color)
        and_bits, xor_bits = c.create_string_buffer(bytes(mask)), c.create_string_buffer(bytes(pixels))
        icon = self.user.CreateIcon(self.instance, 32, 32, 1, 32, and_bits, xor_bits)
        if not icon:
            raise c.WinError(c.get_last_error())
        self.icons.append(icon)
        return icon

    def notify(self, operation=1):
        text, pending = self.controller.status()
        self.notice.tip = text[:127]
        self.notice.icon = self.icons[1 if pending else 0]
        if not self.shell.Shell_NotifyIconW(operation, self.c.byref(self.notice)):
            if operation == 0:
                raise self.c.WinError(self.c.get_last_error())

    def show_error(self, error):
        logger.error("托盘操作未完成：%s", error)
        self.user.MessageBoxW(self.hwnd, str(error), "本地 RAG", 0x10)

    def menu(self):
        w, c = self.w, self.c
        menu = self.user.CreatePopupMenu()
        try:
            text, pending = self.controller.status()
            self.user.AppendMenuW(menu, 3, 0, text)
            self.user.AppendMenuW(menu, 0x800, 0, None)
            self.user.AppendMenuW(menu, 0, 1001, "打开项目网页")
            self.user.AppendMenuW(menu, 0, 1002, "停止中：查看/强制停止" if pending else "停止项目并退出")
            point = w.POINT()
            self.user.GetCursorPos(c.byref(point))
            self.user.SetForegroundWindow(self.hwnd)
            selected = self.user.TrackPopupMenuEx(menu, 0x100 | 0x2, point.x, point.y, self.hwnd, None)
            self.user.PostMessageW(self.hwnd, 0, 0, 0)
            if selected == 1001:
                self.controller.open_page()
            elif selected == 1002:
                self.controller.request_stop()
                self.notify()
        finally:
            self.user.DestroyMenu(menu)

    def tick(self):
        if not self.controller.owned():
            self.user.DestroyWindow(self.hwnd)
            return
        while not self.messages.empty():
            self.show_error(self.messages.get())
        self.notify()
        if self.controller.force_confirmation_due():
            self.controller.confirmed_once = True
            answer = self.user.MessageBoxW(self.hwnd, "后台仍在等待任务结束。是否强制停止本目录实例？\n未完成的上传可能需要重新上传。", "本地 RAG", 0x4 | 0x100 | 0x30)
            if answer == 6:
                def force():
                    try:
                        self.controller.force_stop()
                    except Exception as error:
                        logger.exception("托盘强制停止失败")
                        self.messages.put(str(error))
                threading.Thread(target=force, name="tray-stop", daemon=True).start()

    def window_proc(self, hwnd, message, wp, lp):
        try:
            if message == 0x8001:
                if lp == 0x205:  # WM_RBUTTONUP
                    self.menu()
                elif lp == 0x203:  # WM_LBUTTONDBLCLK
                    self.controller.open_page()
                return 0
            if message == 0x113:
                self.tick()
                return 0
            if message == self.taskbar_created:
                self.notify(0)
                return 0
            if message == 0x11:  # WM_QUERYENDSESSION
                self.controller.request_stop()
                return 1
            if message == 2:
                self.user.PostQuitMessage(0)
                return 0
        except Exception as error:
            logger.exception("托盘回调失败")
            self.show_error(error)
            return 0
        return self.user.DefWindowProcW(hwnd, message, wp, lp)

    def run(self):
        c, w = self.c, self.w
        cls = self.WindowClass(instance=self.instance, name=self.class_name, proc=self.callback)
        if not self.user.RegisterClassW(c.byref(cls)):
            raise c.WinError(c.get_last_error())
        try:
            self.hwnd = self.user.CreateWindowExW(0, self.class_name, "本地 RAG", 0, 0, 0, 0, 0, None, None, self.instance, None)
            if not self.hwnd:
                raise c.WinError(c.get_last_error())
            self.make_icon(False); self.make_icon(True)
            self.notice = self.Notify(size=c.sizeof(self.Notify), window=self.hwnd, id=1, flags=7, message=0x8001)
            self.notify(0)
            if not self.user.SetTimer(self.hwnd, 1, 1000, None):
                raise c.WinError(c.get_last_error())
            from launcher.portable import process_record
            saved = process_record(self.controller.root, self.controller.record["instance_id"], icon_ready=True,
                                   port=self.controller.record["port"], hwnd=int(self.hwnd))
            write_json(self.controller.root, "state/tray.json", saved)
            message = w.MSG()
            while (result := self.user.GetMessageW(c.byref(message), None, 0, 0)) > 0:
                self.user.TranslateMessage(c.byref(message))
                self.user.DispatchMessageW(c.byref(message))
            if result < 0:
                raise c.WinError(c.get_last_error())
        finally:
            if hasattr(self, "notice"):
                self.shell.Shell_NotifyIconW(2, c.byref(self.notice))
            if self.hwnd and self.user.IsWindow(self.hwnd):
                self.user.KillTimer(self.hwnd, 1)
                self.user.DestroyWindow(self.hwnd)
            for icon in self.icons:
                self.user.DestroyIcon(icon)
            self.user.UnregisterClassW(self.class_name, self.instance)


def run(root: Path, instance_id: str):
    with file_lock(root, "tray.lock"):
        record = read_record(root)
        if not record or record["instance_id"] != instance_id or not same_process(root, record):
            raise RuntimeError("托盘所绑定的后台实例已变化")
        try:
            NativeTray(Controller(root, record)).run()
        finally:
            with suppress(OSError, ValueError, KeyError):
                path = safe_path(root, "state/tray.json")
                saved = read_json(path)
                if saved.get("pid") == os.getpid() and saved.get("instance_id") == instance_id:
                    path.unlink()
