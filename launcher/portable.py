"""Windows portable start/CPU/stop/basic maintenance. No system installation."""
from __future__ import annotations

import argparse
from contextlib import suppress
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import uuid
import webbrowser

sys.dont_write_bytecode = True

from launcher.common import (
    file_lock, forget_record, get_health, load_assets, matches_health, read_json,
    read_record, root_id, safe_path, same_process, service_locked, stop_path,
    valid_instance, validate_assets, write_json,
)


class StartCancelled(RuntimeError):
    pass


def confirm_cpu(reason: str, accepted: bool = False) -> bool:
    print(f"GPU启动不可用：{reason}\nCPU备用仍保留混合检索和重排，耗时可能较长。", flush=True)
    return accepted or input("是否使用CPU继续？[y/N] ").strip().lower() in {"y", "yes", "是"}


def browser_url(record: dict) -> str:
    return f"http://127.0.0.1:{record['port']}/settings?build={record['frontend_build_id']}"


def controller(root: Path) -> dict | None:
    path = safe_path(root, "state/start.json")
    return read_json(path) if path.exists() else None


def cancelled(root: Path, owner: dict) -> bool:
    return safe_path(root, f"state/cancel-{valid_instance(owner['instance_id'])}.json").exists()


def check_cancelled(root: Path, owner: dict) -> None:
    if cancelled(root, owner):
        raise StartCancelled("已取消本次启动")


def process_record(root: Path, instance_id: str, **values) -> dict:
    import psutil

    return {"instance_id": instance_id, "root_id": root_id(root), "pid": os.getpid(),
            "created_at": psutil.Process().create_time(), **values}


def inspect_gpu(root: Path, owner: dict) -> dict:
    command = [str(root / "runtime/python.exe"), str(root / "launcher/portable.py"), "inspect-device"]
    child = subprocess.Popen(command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        deadline = time.monotonic() + 60
        while child.poll() is None:
            check_cancelled(root, owner)
            if time.monotonic() >= deadline:
                raise RuntimeError("GPU环境检查超时；查看日志后重试或使用CPU启动入口")
            time.sleep(0.1)
        output, errors = child.communicate()
        write_json(root, "logs/device-check.json", {"returncode": child.returncode,
                   "stderr": errors.decode("utf-8", errors="replace")[-4000:]})
        return json.loads(output.decode("utf-8").strip().splitlines()[-1])
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)


def available_port(port: int) -> None:
    with socket.socket() as listener:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind(("127.0.0.1", port))
        except OSError as error:
            raise RuntimeError(f"本机端口{port}已占用或不可用；未认领或停止占用程序") from error


def bind_listener(port: int | None):
    """Reserve the actual listening socket; auto fallback has no check/bind race."""
    listener = socket.socket()
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        try:
            listener.bind(("127.0.0.1", 8000 if port is None else port))
        except OSError:
            if port is not None:
                raise
            listener.bind(("127.0.0.1", 0))
        return listener
    except OSError as error:
        listener.close()
        raise RuntimeError("无法绑定本机端口；明确指定的端口不会被自动替换") from error


def ensure_tray(root: Path, record: dict, disabled: bool) -> dict:
    if disabled:
        return {"outcome": "disabled"}
    from launcher.tray import ensure

    try:
        result = ensure(root, record)
    except Exception as error:
        print(f"托盘初始化异常：{error}", file=sys.stderr, flush=True)
        result = {"outcome": "failed"}
    if result["outcome"] == "failed":
        print("后台已启动，但托盘入口未就绪；请查看logs/tray.log，仍可使用停止.cmd。", flush=True)
    return result


def _wait_ready(root: Path, child, instance_id: str, owner: dict, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        check_cancelled(root, owner)
        if child.poll() is not None:
            raise RuntimeError(f"后端提前退出（{child.returncode}），请查看logs/backend.error.log")
        record = read_record(root)
        if record and record["instance_id"] == instance_id and same_process(root, record):
            if stop_path(root, record).exists():
                raise StartCancelled("本次服务已收到停止请求")
            health = get_health(record["port"])
            if matches_health(root, record, health):
                warmup = health.get("model_warmup", {})
                if warmup.get("status") == "failed":
                    raise RuntimeError("MODEL_WARMUP:" + json.dumps(warmup, ensure_ascii=False))
                if warmup.get("status") == "ready":
                    print(f"模型已就绪；Sparse状态：{health.get('retrieval', {}).get('sparse_index_status', 'unknown')}", flush=True)
                    return record
        time.sleep(0.2)
    raise RuntimeError("等待后端/模型就绪超时，请查看logs/backend.error.log")


def _cleanup_child(root: Path, child, instance_id: str, *, force: bool) -> None:
    record = None
    with suppress(OSError, ValueError, KeyError):
        record = read_record(root)
        if record and record["instance_id"] == instance_id and same_process(root, record):
            write_json(root, f"state/stop-{instance_id}.json", {"instance_id": instance_id, "root_id": root_id(root)})
    try:
        child.wait(timeout=15)
    except subprocess.TimeoutExpired:
        if not force:
            raise RuntimeError("停止仍在等待任务完成；可稍后重试停止，或显式确认强制结束")
        # Popen retains this exact Windows process handle, not a name/PID search.
        child.kill()
        child.wait(timeout=10)
    if record and record["instance_id"] == instance_id:
        with suppress(OSError, ValueError, KeyError):
            forget_record(root, record)


def _start_attempt(root: Path, manifest: dict, asset_id: str, owner: dict, *,
                   port: int | None, device: str, batch_profile: str, timeout: float) -> dict:
    check_cancelled(root, owner)
    if port is not None:
        available_port(port)
    instance_id = uuid.uuid4().hex
    command = [str(root / "runtime/python.exe"), str(root / "launcher/portable.py"), "serve",
               "--instance-id", instance_id, "--asset-id", asset_id, "--port", str(port or 0),
               "--device", device, "--batch-profile", batch_profile]
    with safe_path(root, "logs/backend.out.log").open("ab") as stdout, safe_path(root, "logs/backend.error.log").open("ab") as stderr:
        child = subprocess.Popen(command, cwd=root, stdout=stdout, stderr=stderr,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        return _wait_ready(root, child, instance_id, owner, timeout)
    except BaseException as error:
        _cleanup_child(root, child, instance_id, force=not isinstance(error, StartCancelled))
        raise


def start(root: Path, *, cpu: bool = False, port: int | None = None, no_browser: bool = False, no_tray: bool = False,
          accept_cpu_fallback: bool = False, timeout: float = 180, update_id: str | None = None) -> dict:
    from launcher.updater import guard_start

    guard_start(root, update_id)
    load_assets(root)  # Source trees fail before creating state or personal data.
    safe_path(root, "state").mkdir(exist_ok=True)
    with file_lock(root, "command.lock", timeout=480):
        guard_start(root, update_id)
        old = read_record(root)
        if old and same_process(root, old):
            _, current_asset_id = validate_assets(root)
            health = get_health(old["port"])
            if (old["asset_id"] != current_asset_id or not matches_health(root, old, health)
                    or health.get("model_warmup", {}).get("status") != "ready"):
                raise RuntimeError("已有同目录服务但版本/健康身份不匹配，请先停止并核对日志")
            if cpu and old["device"] != "cpu":
                print("当前已有GPU实例；切换CPU须先停止。本次仅打开原实例。", flush=True)
            if not no_browser:
                webbrowser.open(browser_url(old))
            return {"outcome": "reused", **old, "tray": ensure_tray(root, old, no_tray)}
        if service_locked(root):
            raise RuntimeError("本目录仍有服务锁，未覆盖实例记录或启动第二份服务")
        if old:
            safe_path(root, "state/service.json").rename(safe_path(root, f"state/stale-{uuid.uuid4().hex}.json"))
        owner = process_record(root, uuid.uuid4().hex, action="start")
        write_json(root, "state/start.json", owner)
        try:
            print("正在校验随包资源；首次会读取完整模型，后续复用未变化文件的校验结果。", flush=True)
            manifest, asset_id = validate_assets(root)
            check_cancelled(root, owner)
            from backend.portable import prepare_environment

            prepare_environment(root, "cpu" if cpu else "cuda:0")
            if port is not None:
                available_port(port)
            device = "cpu" if cpu else "cuda:0"
            if not cpu:
                probe = inspect_gpu(root, owner)
                if not probe.get("available"):
                    if probe.get("kind") != "gpu_unavailable":
                        raise RuntimeError("运行环境检查失败（不能当作GPU问题自动回退）：" + str(probe.get("error")))
                    if not confirm_cpu(str(probe.get("error")), accept_cpu_fallback):
                        raise StartCancelled("未确认CPU，未启动服务；可使用CPU启动.cmd")
                    device = "cpu"
            batch_profile = "normal"
            try:
                record = _start_attempt(root, manifest, asset_id, owner, port=port, device=device,
                                        batch_profile=batch_profile, timeout=timeout)
            except RuntimeError as error:
                oom = "MODEL_WARMUP:" in str(error) and any(term in str(error).lower() for term in
                    ("outofmemoryerror", "cuda out of memory", "cublas_status_alloc_failed"))
                if device != "cuda:0" or not oom:
                    raise
                print("GPU显存不足：仅降低batch重试一次，候选/top_k和模型保持不变。", flush=True)
                try:
                    record = _start_attempt(root, manifest, asset_id, owner, port=port, device=device,
                                            batch_profile="reduced", timeout=timeout)
                except RuntimeError as retry_error:
                    if "MODEL_WARMUP:" not in str(retry_error) or not any(term in str(retry_error).lower()
                            for term in ("outofmemoryerror", "cuda out of memory", "cublas_status_alloc_failed")):
                        raise
                    if not confirm_cpu(str(retry_error), accept_cpu_fallback):
                        raise StartCancelled("GPU重试失败，未确认CPU") from retry_error
                    record = _start_attempt(root, manifest, asset_id, owner, port=port, device="cpu",
                                            batch_profile="normal", timeout=timeout)
            check_cancelled(root, owner)
            if not no_browser:
                webbrowser.open(browser_url(record))
            print(f"服务已就绪：{browser_url(record)}\n实际模式：{record['device']} / {record['batch_profile']}；关闭浏览器不会停止服务。", flush=True)
            return {"outcome": "started", **record, "tray": ensure_tray(root, record, no_tray)}
        finally:
            current = controller(root)
            if current and current["instance_id"] == owner["instance_id"]:
                safe_path(root, "state/start.json").unlink(missing_ok=True)
            safe_path(root, f"state/cancel-{owner['instance_id']}.json").unlink(missing_ok=True)


def stop(root: Path, *, timeout: float = 30, force: bool = False, expected_instance_id: str | None = None,
         allow_prompt: bool = True) -> dict:
    record = read_record(root)
    if expected_instance_id is not None and (not record or record["instance_id"] != expected_instance_id):
        return {"outcome": "instance_changed"}
    starting = controller(root)
    if starting and same_process(root, starting, action="start"):
        write_json(root, f"state/cancel-{valid_instance(starting['instance_id'])}.json", {"cancel": True})
    if not record or not same_process(root, record):
        if service_locked(root):
            raise RuntimeError("服务锁存在但进程身份无法确认，未发送停止或强杀请求")
        return {"outcome": "cancellation_requested" if starting and same_process(root, starting, action="start") else "not_running"}
    write_json(root, f"state/stop-{record['instance_id']}.json", {"instance_id": record["instance_id"], "root_id": root_id(root)})
    deadline = time.monotonic() + timeout
    while same_process(root, record) and time.monotonic() < deadline:
        time.sleep(0.2)
    if same_process(root, record):
        if not force and not allow_prompt:
            raise RuntimeError("任务仍在执行，更新未强制结束项目；正常停止后可再次更新")
        if not force and input("服务仍在等待任务结束，是否强制结束本实例？[y/N] ").strip().lower() not in {"y", "yes", "是"}:
            raise RuntimeError("尚未停止；停止请求已保留，不要移动或移除目录")
        import psutil

        if not same_process(root, record):
            raise RuntimeError("进程身份已变化，拒绝强制结束")
        process = psutil.Process(record["pid"])
        if not same_process(root, record, process=process):
            raise RuntimeError("捕获的进程身份已变化，拒绝强制结束")
        process.terminate()
        process.wait(timeout=10)
    forget_record(root, record)
    return {"outcome": "stopped", "instance_id": record["instance_id"]}


def inspect_device() -> int:
    try:
        import torch

        if not torch.cuda.is_available():
            result = {"available": False, "kind": "gpu_unavailable", "error": "CUDA不可用或无兼容驱动"}
        else:
            try:
                value = torch.ones(1, device="cuda:0")
                torch.cuda.synchronize()
                del value
                result = {"available": True, "gpu": torch.cuda.get_device_name(0)}
            except RuntimeError as error:
                result = {"available": False, "kind": "gpu_unavailable", "error": str(error)}
    except Exception as error:
        result = {"available": False, "kind": "runtime_error", "error": f"{type(error).__name__}: {error}"}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


def serve(root: Path, args) -> None:
    import psutil
    import uvicorn

    valid_instance(args.instance_id)
    manifest, asset_id = load_assets(root)
    if asset_id != args.asset_id:
        raise ValueError("启动前后资源清单已变化，拒绝启动")
    os.environ.update(RAG_DISTRIBUTION_MODE="portable", RAG_PORTABLE_DEVICE=args.device,
                      RAG_PORTABLE_BATCH_PROFILE=args.batch_profile, PYTHONDONTWRITEBYTECODE="1")
    with file_lock(root, "service.lock"), bind_listener(None if args.port == 0 else args.port) as listener:
        actual_port = listener.getsockname()[1]
        record = process_record(root, args.instance_id, port=actual_port, asset_id=asset_id,
                                frontend_build_id=manifest["frontend_build_id"], device=args.device,
                                batch_profile=args.batch_profile)
        write_json(root, "state/service.json", record)
        from backend.main import app

        app.state.portable_instance = {name: record[name] for name in
            ("instance_id", "root_id", "asset_id", "frontend_build_id", "pid")}
        app.state.portable_port = actual_port
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=actual_port, workers=1,
                    proxy_headers=False, access_log=False, server_header=False, log_level="info"))
        finished = threading.Event()
        def monitor():
            while not finished.wait(0.2):
                request_path = stop_path(root, record)
                if request_path.exists():
                    with suppress(OSError, ValueError):
                        payload = read_json(request_path)
                        if payload.get("instance_id") == record["instance_id"] and payload.get("root_id") == record["root_id"]:
                            server.should_exit = True
        thread = threading.Thread(target=monitor, name="portable-stop", daemon=True)
        thread.start()
        try:
            server.run(sockets=[listener])
        finally:
            finished.set()
            thread.join(timeout=2)
            forget_record(root, record)


CACHE_FILES = {"分词缓存": ("temp/jieba.cache", "cache/jieba.cache"),
               "资源校验缓存": ("state/verified-assets.json",)}


def maintain(root: Path, action: str, *, yes: bool = False, force: bool = False) -> dict:
    load_assets(root)
    if action in {"directory", "logs"}:
        directory = safe_path(root, "logs") if action == "logs" else root
        directory.mkdir(exist_ok=True)
        os.startfile(directory)
        return {"outcome": "opened", "directory": action}
    with file_lock(root, "command.lock", timeout=5):
        if action == "prepare-remove":
            stop(root, force=force)
        record = read_record(root)
        if service_locked(root) or (record and same_process(root, record)):
            raise RuntimeError("请先停止本目录服务；未清理文件")
        if action == "prepare-remove":
            message = "本应用服务已退出。按需先备份到目录外，再手动删除整个目录；会同时删除data、模型和运行环境，不会替你清理外部原件或备份。"
            print(message, flush=True)
            return {"outcome": "ready_for_manual_removal", "message": message}
        candidates = [(kind, safe_path(root, relative)) for kind, values in CACHE_FILES.items()
                      for relative in values if safe_path(root, relative).is_file()]
        for kind, path in candidates:
            print(f"{kind}：{path.relative_to(root)} ({path.stat().st_size} bytes)", flush=True)
        print("仅清理以上文件；其他缓存/临时文件、data、模型、OCR、日志和未知文件全部保留。", flush=True)
        if not yes and input("确认清理以上可重建文件？[y/N] ").strip().lower() not in {"y", "yes", "是"}:
            return {"outcome": "cancelled", "files": []}
        for _, path in candidates:
            path.unlink()
        return {"outcome": "cache_cleared", "files": [path.relative_to(root).as_posix() for _, path in candidates]}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    starter = commands.add_parser("start")
    starter.add_argument("--cpu", action="store_true")
    starter.add_argument("--no-browser", action="store_true")
    starter.add_argument("--no-tray", action="store_true", help="不创建托盘入口（自动化/无桌面验证）")
    starter.add_argument("--accept-cpu-fallback", action="store_true")
    starter.add_argument("--port", type=int, default=None, help="指定端口；省略时优先8000，占用则由系统分配")
    starter.add_argument("--timeout", type=float, default=180)
    stopper = commands.add_parser("stop")
    stopper.add_argument("--force", action="store_true")
    stopper.add_argument("--timeout", type=float, default=30)
    worker = commands.add_parser("serve")
    worker.add_argument("--instance-id", required=True)
    worker.add_argument("--asset-id", required=True)
    worker.add_argument("--port", type=int, default=8000)
    worker.add_argument("--device", choices=["cpu", "cuda:0"], required=True)
    worker.add_argument("--batch-profile", choices=["normal", "reduced"], default="normal")
    commands.add_parser("inspect-device")
    tray = commands.add_parser("tray")
    tray.add_argument("--instance-id", required=True)
    maintenance = commands.add_parser("maintain")
    maintenance.add_argument("--action", choices=["directory", "logs", "cache", "prepare-remove"])
    maintenance.add_argument("--yes", action="store_true")
    maintenance.add_argument("--force", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if os.name != "nt" or not sys.flags.isolated or Path(sys.executable).resolve() != (root / "runtime/python.exe").resolve():
        parser.error("请使用此便携目录自带的Windows解释器和CMD入口")
    if (hasattr(args, "port") and args.port is not None and not 1024 <= args.port <= 65535
            and not (args.command == "serve" and args.port == 0)):
        parser.error("端口必须在1024到65535之间")
    sys.dont_write_bytecode = True
    try:
        if args.command == "start":
            result = start(root, cpu=args.cpu, port=args.port, no_browser=args.no_browser, no_tray=args.no_tray,
                           accept_cpu_fallback=args.accept_cpu_fallback, timeout=args.timeout)
        elif args.command == "stop":
            result = stop(root, timeout=args.timeout, force=args.force)
        elif args.command == "serve":
            serve(root, args)
            return 0
        elif args.command == "inspect-device":
            from backend.portable import prepare_environment

            prepare_environment(root, "cuda:0")
            return inspect_device()
        elif args.command == "tray":
            from launcher.tray import run

            run(root, args.instance_id)
            return 0
        else:
            action = args.action
            if action is None:
                print("1 打开目录  2 打开日志  3 清理可重建缓存  4 移除前准备  0 退出")
                action = {"1": "directory", "2": "logs", "3": "cache", "4": "prepare-remove"}.get(input("请选择：").strip())
                if action is None:
                    return 0
            result = maintain(root, action, yes=args.yes, force=args.force)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    except (Exception, KeyboardInterrupt) as error:
        print(f"操作未完成：{error}\n请查看本目录logs；不要在服务未退出时移动或删除目录。", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
