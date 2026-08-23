"""Run the fixed 120-question CRUD_RAG retrieval baseline without an LLM.

All generated indexes, metadata and reports stay below ``v3/runs``.  The
``cpu`` and ``gpu`` profiles deliberately share every retrieval parameter;
only the neural inference device/precision changes.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FIXTURE_ROOT = ROOT.parent
PROJECT_ROOT = FIXTURE_ROOT.parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def build_eval_argv(*, label: str, profile: str, overwrite: bool) -> list[str]:
    profile_args = {
        "cpu": [
            "--embedding-device",
            "cpu",
            "--reranker-device",
            "cpu",
            "--no-reranker-fp16",
        ],
        "gpu": [
            "--embedding-device",
            "cuda:0",
            "--reranker-device",
            "cuda:0",
            "--reranker-fp16",
        ],
    }
    arguments = [
        "retrieval-sweep",
        "--label",
        label,
        "--dataset",
        str(ROOT / "questions_v3_120q.json"),
        "--corpus",
        str(FIXTURE_ROOT / "corpus"),
        "--manifest",
        str(FIXTURE_ROOT / "corpus_v3_manifest.json"),
        "--allow-draft-v3",
        "--retrieval-trace",
        "--candidate-ks",
        "15",
        "--embedding-model",
        "BAAI/bge-large-zh-v1.5",
        "--embedding-batch-size",
        "32",
        "--query-instruction",
        "",
        "--top-k",
        "5",
        "--retrieval-mode",
        "hybrid",
        "--distance-threshold",
        "0.67",
        "--bm25-top-k",
        "15",
        "--bm25-k1",
        "1.5",
        "--bm25-b",
        "0.75",
        "--rrf-k",
        "60",
        "--reranker-model",
        "BAAI/bge-reranker-v2-m3",
        "--reranker-batch-size",
        "8",
        "--chunk-strategy",
        "recursive",
        "--chunk-size",
        "400",
        "--chunk-overlap",
        "80",
        "--chunk-max-tokens",
        "480",
        *profile_args[profile],
    ]
    if overwrite:
        arguments.append("--overwrite")
    return arguments


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CRUD_RAG 120 题固定检索基线")
    parser.add_argument("--label", required=True)
    parser.add_argument("--profile", choices=("cpu", "gpu"), required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    # This workflow is retrieval-only.  Keep model resolution offline and pin
    # every artifact to the fixture directory before backend modules import.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    from eval import eval as eval_cli

    eval_cli.EVAL_DIR = ROOT
    original_argv = sys.argv
    try:
        sys.argv = [
            str(Path(__file__).name),
            *build_eval_argv(
                label=args.label,
                profile=args.profile,
                overwrite=args.overwrite,
            ),
        ]
        eval_cli.main()
    finally:
        sys.argv = original_argv
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
