"""Recompute retrieval metrics from saved JSONL/Trace without model inference."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from eval.retrieval_sweep import (
    _recommend_candidate,
    build_candidate_result,
    render_retrieval_sweep,
)


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def rescore_run(*, run_dir: Path, dataset_path: Path) -> dict:
    """Rescore a completed retrieval run while preserving its raw artifacts."""
    report_path = run_dir / "retrieval-sweep.json"
    markdown_path = run_dir / "retrieval-sweep.md"
    report = _load_json(report_path)
    dataset = _load_json(dataset_path)
    questions = dataset["questions"]
    expected_ids = [question["id"] for question in questions]

    candidates = []
    for old_candidate in report["candidates"]:
        candidate_k = int(old_candidate["candidate_k"])
        raw_path = run_dir / f"retrieval-k{candidate_k}.jsonl"
        raw_rows = [
            json.loads(line)
            for line in raw_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        raw_ids = [row["id"] for row in raw_rows]
        if raw_ids != expected_ids:
            raise ValueError(
                f"{raw_path} 的题目 ID/顺序与 dataset 不一致："
                f"expected={len(expected_ids)}, actual={len(raw_ids)}"
            )
        retrievals = {row["id"]: row["retrieval"] for row in raw_rows}
        traces = {row["id"]: row.get("trace") for row in raw_rows}
        candidates.append(
            build_candidate_result(
                candidate_k=candidate_k,
                questions=questions,
                retrievals=retrievals,
                timings_s=[float(row["retrieval_s"]) for row in raw_rows],
                traces=traces,
            )
        )

    backup_json = run_dir / "retrieval-sweep.pre-rescore.json"
    backup_markdown = run_dir / "retrieval-sweep.pre-rescore.md"
    if not backup_json.exists():
        shutil.copy2(report_path, backup_json)
    if markdown_path.exists() and not backup_markdown.exists():
        shutil.copy2(markdown_path, backup_markdown)

    report["candidates"] = candidates
    report["recommended_candidate_k"] = _recommend_candidate(candidates)
    report["rescore"] = {
        "rescored_at": datetime.now().isoformat(timespec="seconds"),
        "source": "saved retrieval JSONL/Trace; no model inference; no LLM",
        "metric_revision": "dataset-v3-unit-index-alignment-v1",
        "dataset": str(dataset_path.resolve()),
    }

    json_temp = report_path.with_suffix(".json.tmp")
    md_temp = markdown_path.with_suffix(".md.tmp")
    json_temp.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_temp.write_text(render_retrieval_sweep(report), encoding="utf-8")
    os.replace(json_temp, report_path)
    os.replace(md_temp, markdown_path)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="从已保存 JSONL/Trace 重新计算检索指标")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--dataset", required=True, type=Path)
    args = parser.parse_args()
    report = rescore_run(run_dir=args.run_dir, dataset_path=args.dataset)
    print(
        f"rescored {report['label']}: "
        f"{sum(row['question_count'] for row in report['candidates'])} rows"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
