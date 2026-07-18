"""P1-4 检索距离阈值配置与离线校准测试。"""

import json
import os
from types import SimpleNamespace

from backend.config import settings
from backend.services import retriever
from eval import adapters
from eval import eval as eval_cli
from eval.threshold import build_threshold_sweep, render_threshold_report


def _result(distance, content="目标证据", document_name="doc.txt"):
    return {
        "chunk_id": f"chunk-{distance}",
        "content": content,
        "metadata": {
            "document_name": document_name,
            "page_number": 1,
        },
        "distance": distance,
    }


def test_retrieve_uses_configured_distance_threshold(monkeypatch):
    raw = [_result(0.20), _result(0.68), _result(0.90)]
    calls = []

    def fake_search(question, top_k):
        calls.append((question, top_k))
        return raw

    monkeypatch.setattr(retriever, "vector_search", fake_search)
    monkeypatch.setattr(
        settings, "RETRIEVAL_DISTANCE_THRESHOLD", 0.67, raising=False
    )

    results = retriever.retrieve("测试问题", top_k=2)

    assert calls == [("测试问题", 4)]
    assert [row["distance"] for row in results] == [0.20]


def test_threshold_sweep_reports_quality_and_gating_rates():
    questions = [
        {
            "id": "a1",
            "category": "fact",
            "answerable": True,
            "evidence": [
                {"document_name": "doc.txt", "snippet": "目标证据"}
            ],
        },
        {
            "id": "a2",
            "category": "fact",
            "answerable": True,
            "evidence": [
                {"document_name": "doc.txt", "snippet": "另一条证据"}
            ],
        },
        {
            "id": "u1",
            "category": "unanswerable",
            "answerable": False,
            "evidence": [],
        },
    ]
    answers = {
        "a1": {"retrieval": [_result(0.40)]},
        "a2": {"retrieval": [_result(0.55, content="另一条证据")]},
        "u1": {"retrieval": [_result(0.75, content="无关内容")]},
    }

    payload = build_threshold_sweep(
        label="unit-sweep",
        questions=questions,
        answers=answers,
        thresholds=[0.50, 0.60, 0.65, 0.70, 0.80],
        source="raw_answers.jsonl",
    )

    by_threshold = {row["threshold"]: row for row in payload["candidates"]}
    assert by_threshold[0.50]["answerable_empty_ids"] == ["a2"]
    assert by_threshold[0.50]["threshold_false_refusal_rate"] == 0.5
    assert by_threshold[0.60]["hit_rate"] == 1.0
    assert by_threshold[0.60]["recall"] == 1.0
    assert by_threshold[0.60]["mrr"] == 1.0
    assert by_threshold[0.60]["unanswerable_llm_entry_rate"] == 0.0
    assert by_threshold[0.80]["unanswerable_llm_entry_ids"] == ["u1"]
    assert by_threshold[0.80]["unanswerable_llm_entry_rate"] == 1.0
    # 0.60/0.65/0.70 同属最优平台，取中点，避免贴近任一边界。
    assert payload["recommended_threshold"] == 0.65


def test_threshold_report_explains_recommendation():
    questions = [
        {
            "id": "a1",
            "category": "fact",
            "answerable": True,
            "evidence": [
                {"document_name": "doc.txt", "snippet": "目标证据"}
            ],
        },
        {
            "id": "u1",
            "category": "unanswerable",
            "answerable": False,
            "evidence": [],
        },
    ]
    answers = {
        "a1": {"retrieval": [_result(0.50)]},
        "u1": {"retrieval": [_result(0.70, content="无关内容")]},
    }
    payload = build_threshold_sweep(
        label="report-sweep",
        questions=questions,
        answers=answers,
        thresholds=[0.55, 0.60, 0.65, 0.75],
        source="raw_answers.jsonl",
    )

    report = render_threshold_report(payload)

    assert "| 阈值 | 检索命中率" in report
    assert "阈值诱发误拒答率" in report
    assert "不可回答题进入 LLM 比例" in report
    assert "推荐阈值：`0.60`" in report


def test_selected_threshold_is_the_project_default():
    assert settings.RETRIEVAL_DISTANCE_THRESHOLD == 0.67


def test_eval_run_sets_and_records_distance_threshold(monkeypatch, tmp_path):
    monkeypatch.setattr(eval_cli, "EVAL_DIR", tmp_path)
    monkeypatch.setenv("RETRIEVAL_DISTANCE_THRESHOLD", "1.5")
    args = SimpleNamespace(
        label="threshold-config-test",
        overwrite=False,
        embedding_model=None,
        query_instruction=None,
        top_k=None,
        distance_threshold=0.67,
    )

    eval_cli._setup_env(args)

    assert os.environ["RETRIEVAL_DISTANCE_THRESHOLD"] == "0.67"
    monkeypatch.setattr(
        settings, "RETRIEVAL_DISTANCE_THRESHOLD", 0.67, raising=False
    )
    assert adapters.get_run_config()["retrieval_distance_threshold"] == 0.67


def test_threshold_sweep_cli_writes_reproducible_reports(tmp_path):
    dataset_path = tmp_path / "questions.json"
    raw_path = tmp_path / "raw_answers.jsonl"
    output_dir = tmp_path / "sweep"
    dataset_path.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "id": "a1",
                        "category": "fact",
                        "answerable": True,
                        "evidence": [
                            {
                                "document_name": "doc.txt",
                                "snippet": "目标证据",
                            }
                        ],
                    },
                    {
                        "id": "u1",
                        "category": "unanswerable",
                        "answerable": False,
                        "evidence": [],
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    raw_path.write_text(
        "\n".join(
            json.dumps(row, ensure_ascii=False)
            for row in [
                {"id": "a1", "retrieval": [_result(0.50)]},
                {"id": "u1", "retrieval": [_result(0.70, content="无关内容")]},
            ]
        ),
        encoding="utf-8",
    )
    args = SimpleNamespace(
        label="cli-sweep",
        dataset=str(dataset_path),
        raw_answers=str(raw_path),
        thresholds=[0.55, 0.60, 0.65, 0.75],
        output_dir=str(output_dir),
    )

    eval_cli.cmd_threshold_sweep(args)

    payload = json.loads((output_dir / "threshold-sweep.json").read_text("utf-8"))
    report = (output_dir / "threshold-sweep.md").read_text("utf-8")
    assert payload["recommended_threshold"] == 0.60
    assert payload["source"] == str(raw_path)
    assert "推荐阈值：`0.60`" in report
