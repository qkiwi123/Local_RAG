"""Independent offline update entry, also bootstraps pre-updater portable ZIPs."""
from pathlib import Path
import argparse
import json
import sys
import zipfile

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from launcher import updater


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="保留数据的本地应用更新")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--job")
    parser.add_argument("--recover", action="store_true")
    args = parser.parse_args()
    try:
        if args.root.is_symlink() or args.root.is_junction():
            raise ValueError("请选择实际便携目录，不使用目录链接")
        root = args.root.resolve()
        if args.job:
            state = updater.run_update(root, args.job)
        elif args.recover or updater.active(root):
            previous = updater.active(root)
            if not previous:
                print("没有未完成的更新。")
                return 0
            if input("发现未完成的更新，恢复到更新前的程序和对应数据？[y/N] ").strip().lower() not in {"y", "yes", "是"}:
                return 0
            with updater.file_lock(root, "update.lock", timeout=2):
                updater.rollback(root, previous["id"])
            state = updater.read_json(updater.job_path(root, previous["id"]) / "journal.json")
        else:
            package = args.package
            if args.directory:
                # A ZIP can be extracted and double-clicked on an older install.
                directory = args.directory.resolve()
                manifest = json.loads((directory / "update-manifest.json").read_text("utf-8"))
                names = ["update-manifest.json"] + ["payload/" + updater.normalized(e["path"]) for e in manifest["files"]]
                names += [updater.normalized(e["path"]) for e in manifest["bootstrap"]]
                incoming = updater.safe_path(root, "state/update-bootstrap")
                incoming.mkdir(parents=True, exist_ok=True)
                package = incoming / (updater.uuid.uuid4().hex + ".zip")
                with zipfile.ZipFile(package, "x", zipfile.ZIP_DEFLATED) as archive:
                    for name in names:
                        path = updater.safe_path(directory, name)
                        archive.write(path, name)
            if package is None:
                raise ValueError("请选择更新ZIP，或运行更新包内的安装更新.cmd")
            state = updater.stage(root, package.resolve())
            print(f"目标版本：{state['version']}\n更新说明：{state['notes']}\n项目目录：{root}")
            print("将正常停止项目，备份程序与整套数据，再更新应用。模型和运行环境继续保留。")
            if input("确认更新？[y/N] ").strip().lower() not in {"y", "yes", "是"}:
                print("已取消，原程序与数据未改。")
                return 0
            updater.queue(root, state["id"], launch=False)
            # Freeze imports before application files are replaced.
            from launcher import portable
            state = updater.run_update(root, state["id"])
        print(state["message"])
        if state.get("error"):
            print("原因：" + state["error"])
        return 0 if state["phase"] in {"completed", "rolled_back"} else 1
    except Exception as error:
        print(f"操作未完成：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
