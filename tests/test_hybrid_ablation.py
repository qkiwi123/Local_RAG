"""P2-1 五组混合检索消融测试。"""

import json
from types import SimpleNamespace

import pytest

from backend.config import settings
from eval import adapters
from eval import eval as eval_cli
from eval.hybrid_ablation import (
    ABLATION_CONFIGS,
    build_hybrid_ablation,
    build_mode_result,
    render_hybrid_ablation,
)


QUESTIONS = [
    {
        "id": "q01",
        "question": "证据是什么？",
        "category": "fact",
        "answerable": True,
        "evidence": [{"document_name": "doc.txt", "snippet": "黄金证据"}],
    },
    {
        "id": "q27",
        "question": "不存在的分数？",
        "category": "unanswerable",
        "answerable": False,
        "evidence": [],
    },
]


def _retrieval(hit=True):
    if not hit:
        return []
    return [
        {
            "chunk_id": "c1",
            "content": "包含黄金证据",
            "metadata": {"document_name": "doc.txt", "page_number": 1},
            "distance": 0.2,
        }
    ]


def _mode(name, hit_rate):
    return {
        "name": name,
        "question_count": 1,
        "hit_rate": hit_rate,
        "recall": hit_rate,
        "mrr": hit_rate,
        "latency_s": {"mean": 0.1, "p50": 0.1, "p95": 0.1},
        "by_category": {},
        "retrieval_failure_ids": [] if hit_rate else ["q01"],
        "unanswerable_llm_entry_ids": [],
        "per_question": [
            {
                "id": "q01",
                "answerable": True,
                "hit": bool(hit_rate),
                "recall": hit_rate,
                "reciprocal_rank": hit_rate,
            }
        ],
        "original_21": {"question_count": 1, "hit_rate": hit_rate},
        "lexical": {"question_count": 0},
    }


def test_ablation_configs_include_required_four_and_deployment_control():
    assert ABLATION_CONFIGS == [
        ("dense", "dense", False),
        ("bm25", "bm25", False),
        ("hybrid", "hybrid", False),
        ("hybrid_reranker", "hybrid", True),
        ("dense_reranker", "dense", True),
    ]


def test_mode_result_splits_original_and_lexical_metrics():
    questions = [
        QUESTIONS[0],
        {
            **QUESTIONS[0],
            "id": "q22",
            "category": "lexical",
        },
    ]
    result = build_mode_result(
        name="hybrid",
        questions=questions,
        retrievals={"q01": _retrieval(), "q22": _retrieval()},
        timings_s=[0.1, 0.2],
    )

    assert result["name"] == "hybrid"
    assert result["hit_rate"] == 1.0
    assert result["original_21"]["hit_rate"] == 1.0
    assert result["lexical"]["hit_rate"] == 1.0


def test_ablation_compares_hybrid_reranker_to_dense_reranker():
    payload = build_hybrid_ablation(
        label="unit",
        config={},
        ingest={"total_chunks": 1, "ingest_s": 0.1},
        results=[
            _mode("dense_reranker", hit_rate=0.0),
            _mode("hybrid_reranker", hit_rate=1.0),
        ],
        dataset_version=2,
    )

    assert payload["deployment_comparison"]["hit_rate_delta"] == 1.0
    assert payload["deployment_comparison"]["improved_ids"] == ["q01"]
    assert "Dense + Reranker" in render_hybrid_ablation(payload)


def test_hybrid_ablation_cli_ingests_once_and_restores_settings(
    monkeypatch, tmp_path
):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    dataset_path = tmp_path / "questions.json"
    dataset_path.write_text(
        json.dumps({"version": 2, "questions": QUESTIONS}, ensure_ascii=False),
        encoding="utf-8",
    )
    calls = {"ingest": 0, "retrieval": []}
    original_mode = settings.RETRIEVAL_MODE
    original_reranker = settings.RERANKER_ENABLED

    monkeypatch.setattr(eval_cli, "_setup_env", lambda args: run_dir)
    monkeypatch.setattr(adapters, "assert_isolation", lambda: None)
    monkeypatch.setattr(adapters, "parse_corpus", lambda corpus: {"doc.txt": "黄金证据"})
    monkeypatch.setattr(eval_cli, "_validate_dataset", lambda dataset, texts: None)

    def fake_ingest(corpus):
        calls["ingest"] += 1
        return {"documents": {"doc.txt": 1}, "total_chunks": 1, "ingest_s": 0.1}

    def fake_retrieve(question):
        calls["retrieval"].append(
            (settings.RETRIEVAL_MODE, settings.RERANKER_ENABLED, question)
        )
        return {"retrieval": _retrieval(hit="证据" in question), "retrieval_s": 0.1}

    monkeypatch.setattr(adapters, "ingest_corpus", fake_ingest)
    monkeypatch.setattr(adapters, "retrieve_only", fake_retrieve)
    monkeypatch.setattr(adapters, "get_run_config", lambda: {"top_k": 5})
    args = SimpleNamespace(
        label="p2-unit",
        dataset=str(dataset_path),
        corpus=str(tmp_path),
    )

    eval_cli.cmd_hybrid_ablation(args)

    assert calls["ingest"] == 1
    assert [pair[:2] for pair in calls["retrieval"]] == [
        (mode, enabled)
        for _, mode, enabled in ABLATION_CONFIGS
        for _ in QUESTIONS
    ]
    assert settings.RETRIEVAL_MODE == original_mode
    assert settings.RERANKER_ENABLED is original_reranker
    assert (run_dir / "hybrid-ablation.json").exists()
    assert (run_dir / "hybrid-ablation.md").exists()


def test_hybrid_ablation_restores_settings_when_retrieval_fails(
    monkeypatch, tmp_path
):
    original_mode = settings.RETRIEVAL_MODE
    original_reranker = settings.RERANKER_ENABLED
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    dataset_path = tmp_path / "questions.json"
    dataset_path.write_text(
        json.dumps({"version": 2, "questions": [QUESTIONS[0]]}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(eval_cli, "_setup_env", lambda args: run_dir)
    monkeypatch.setattr(adapters, "assert_isolation", lambda: None)
    monkeypatch.setattr(adapters, "parse_corpus", lambda corpus: {})
    monkeypatch.setattr(eval_cli, "_validate_dataset", lambda dataset, texts: None)
    monkeypatch.setattr(
        adapters,
        "ingest_corpus",
        lambda corpus: {"documents": {}, "total_chunks": 0, "ingest_s": 0.0},
    )
    monkeypatch.setattr(
        adapters,
        "retrieve_only",
        lambda question: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        eval_cli.cmd_hybrid_ablation(
            SimpleNamespace(
                label="failure",
                dataset=str(dataset_path),
                corpus=str(tmp_path),
            )
        )

    assert settings.RETRIEVAL_MODE == original_mode
    assert settings.RERANKER_ENABLED is original_reranker
