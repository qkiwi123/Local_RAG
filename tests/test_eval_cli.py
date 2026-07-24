import sys
from pathlib import Path

import pytest

import eval.eval as eval_cli


@pytest.mark.parametrize(
    ("question", "row", "expected"),
    [
        ({"answerable": True}, {"hit": True}, "命中 PASS"),
        ({"answerable": True}, {"hit": False}, "命中 FAIL"),
        ({"answerable": False}, {"refusal_correct": True}, "拒答 PASS"),
        ({"answerable": False}, {"refusal_correct": False}, "拒答 FAIL"),
    ],
)
def test_progress_status_is_safe_for_windows_gbk_console(question, row, expected):
    from eval.eval import _format_progress_status

    status = _format_progress_status(question, row)

    assert status == expected
    status.encode("gbk")


@pytest.mark.parametrize(
    ("argv", "handler_name"),
    [
        (["eval", "run", "--label", "test"], "cmd_run"),
        (
            [
                "eval",
                "threshold-sweep",
                "raw.jsonl",
                "--label",
                "test",
                "--thresholds",
                "0.67",
                "--output-dir",
                "out",
            ],
            "cmd_threshold_sweep",
        ),
        (
            [
                "eval",
                "retrieval-sweep",
                "--label",
                "test",
                "--candidate-ks",
                "15",
            ],
            "cmd_retrieval_sweep",
        ),
        (["eval", "hybrid-ablation", "--label", "test"], "cmd_hybrid_ablation"),
    ],
)
def test_all_evaluation_commands_default_to_questions_v2(
    monkeypatch, argv, handler_name
):
    captured = {}

    def capture(args):
        captured["dataset"] = args.dataset

    monkeypatch.setattr(eval_cli, handler_name, capture)
    monkeypatch.setattr(sys, "argv", argv)

    eval_cli.main()

    assert Path(captured["dataset"]).name == "questions_v2.json"
