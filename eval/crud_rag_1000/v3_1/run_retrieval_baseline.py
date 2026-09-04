"""Future v3.1 retrieval entry point; --dry-run never loads the backend.

The existing baseline command always ingests into a fresh isolated index.
This wrapper pins the NEW dataset and output root, and forbids overwrites.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from eval.crud_rag_1000.v3.run_retrieval_baseline import (  # noqa: E402
    build_eval_argv as parent_build_eval_argv,
)


def build_eval_argv(*, label: str, profile: str) -> list[str]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", label):
        raise ValueError("label仅允许字母、数字、短横线和下划线，不能包含路径")
    argv = parent_build_eval_argv(label=label, profile=profile, overwrite=False)
    argv[argv.index("--dataset") + 1] = str(ROOT / "questions_v3_120q.json")
    return argv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="新版v3.1检索入口；本轮只预览、不重跑")
    parser.add_argument("--profile", choices=("cpu", "gpu"), required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--dry-run", action="store_true", help="仅打印路径与参数，不推理、不入库")
    args = parser.parse_args(argv)
    evaluation_args = build_eval_argv(label=args.label, profile=args.profile)
    run_dir = ROOT / "runs" / args.label
    if args.dry_run:
        print(json.dumps({
            "dry_run": True, "dataset": str(ROOT / "questions_v3_120q.json"),
            "run_directory": str(run_dir), "evaluation_args": evaluation_args,
            "index_policy": "fresh_isolated_index_on_actual_run",
            "llm_api_called": False,
        }, ensure_ascii=False, indent=2))
        return 0
    if run_dir.exists():
        raise FileExistsError("运行目录已存在；请使用新label，不覆盖旧结果")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    from eval import eval as eval_cli

    old_dir, old_argv = eval_cli.EVAL_DIR, sys.argv
    try:
        eval_cli.EVAL_DIR = ROOT
        sys.argv = [str(Path(__file__).name), *evaluation_args]
        eval_cli.main()
    finally:
        eval_cli.EVAL_DIR = old_dir
        sys.argv = old_argv
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
