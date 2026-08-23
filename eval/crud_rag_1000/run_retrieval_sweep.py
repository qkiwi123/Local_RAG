"""Run the existing retrieval sweep with every artifact kept in this directory."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def build_eval_argv(user_args: Sequence[str]) -> list[str]:
    """Pin fixture paths while leaving normal retrieval options to ``eval.eval``."""
    fixed_flags = {"--dataset", "--corpus", "--manifest", "--allow-draft-v3"}
    conflicts = sorted(fixed_flags.intersection(user_args))
    if conflicts:
        raise ValueError("以下参数由 CRUD_RAG fixture 固定，不可覆盖: " + ", ".join(conflicts))
    return [
        "retrieval-sweep",
        "--dataset",
        str(ROOT / "questions_v3.json"),
        "--corpus",
        str(ROOT / "corpus"),
        "--manifest",
        str(ROOT / "corpus_v3_manifest.json"),
        "--allow-draft-v3",
        *user_args,
    ]


def main(argv: Sequence[str] | None = None) -> int:
    from eval import eval as eval_cli

    arguments = list(sys.argv[1:] if argv is None else argv)
    eval_cli.EVAL_DIR = ROOT
    original_argv = sys.argv
    try:
        sys.argv = [str(Path(__file__).name), *build_eval_argv(arguments)]
        eval_cli.main()
    finally:
        sys.argv = original_argv
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
