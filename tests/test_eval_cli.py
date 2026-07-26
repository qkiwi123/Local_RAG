import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import eval.eval as eval_cli
from eval.dataset_runtime import normalize_for_evaluation


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


def test_dataset_v3_dialogue_turns_use_gold_standalone_question():
    dataset = {
        "schema_version": 3,
        "dataset_id": "dataset-v3",
        "questions": [],
        "dialogues": [
            {
                "dialogue_id": "d1",
                "question_group": "g1",
                "split": "validation",
                "difficulty": "hard",
                "document_types": ["pptx"],
                "turns": [
                    {
                        "turn_id": "d1-t2",
                        "question": "它有什么作用？",
                        "standalone_question": "RAG 有什么作用？",
                        "answerable": True,
                        "unanswerable_type": None,
                        "evidence": [{"document_name": "a.pptx", "snippet": "检索"}],
                        "keypoints": [["检索"]],
                        "reference_answer": "检索。",
                    }
                ],
                "review": {},
            }
        ],
    }

    normalized = normalize_for_evaluation(dataset)

    question = normalized["questions"][0]
    assert normalized["version"] == "dataset-v3"
    assert question["id"] == "d1-t2"
    assert question["original_question"] == "它有什么作用？"
    assert question["question"] == "RAG 有什么作用？"
    assert question["category"] == "dialogue"
    assert question["split"] == "validation"


def test_setup_env_removes_stale_structured_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(eval_cli, "EVAL_DIR", tmp_path)
    run_dir = tmp_path / "runs" / "retry"
    run_dir.mkdir(parents=True)
    for name in ("metadata.db", "metadata.db-wal", "metadata.db-shm"):
        (run_dir / name).write_text("stale", encoding="utf-8")

    result = eval_cli._setup_env(
        SimpleNamespace(label="retry", overwrite=True)
    )

    assert result == run_dir
    assert not (run_dir / "metadata.db").exists()
    assert not (run_dir / "metadata.db-wal").exists()
    assert not (run_dir / "metadata.db-shm").exists()


def test_v3_runtime_allows_locator_disambiguated_duplicate_snippet():
    dataset = {
        "schema_version": 3,
        "questions": [
            {
                "id": "q1",
                "evidence": [
                    {
                        "document_name": "doc.txt",
                        "snippet": "重复",
                        "unit_id": "doc.txt:u1",
                        "start_char": 2,
                        "end_char": 4,
                    }
                ],
            }
        ],
    }

    eval_cli._validate_dataset(dataset, {"doc.txt": "重复重复"})
