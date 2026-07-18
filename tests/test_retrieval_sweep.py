"""无 LLM 的 Reranker 候选数扫描指标与报告测试。"""

import json
from types import SimpleNamespace

from backend.config import settings
from eval import adapters
from eval import eval as eval_cli

from eval.retrieval_sweep import (
    build_candidate_result,
    build_retrieval_sweep,
    render_retrieval_sweep,
)


def _result(distance, content, document_name="doc.txt", **extra):
    return {
        "chunk_id": f"chunk-{distance}-{content}",
        "content": content,
        "metadata": {
            "document_name": document_name,
            "page_number": 1,
        },
        "distance": distance,
        **extra,
    }


QUESTIONS = [
    {
        "id": "a1",
        "question": "证据一是什么？",
        "category": "fact",
        "answerable": True,
        "evidence": [{"document_name": "doc.txt", "snippet": "证据一"}],
    },
    {
        "id": "a2",
        "question": "两份证据是什么？",
        "category": "multi_doc",
        "answerable": True,
        "evidence": [
            {"document_name": "doc.txt", "snippet": "证据二"},
            {"document_name": "other.txt", "snippet": "证据三"},
        ],
    },
    {
        "id": "u1",
        "question": "域外问题",
        "category": "unanswerable",
        "answerable": False,
        "evidence": [],
    },
]


def test_candidate_result_calculates_retrieval_quality_and_latency():
    retrievals = {
        "a1": [
            _result(0.1, "干扰"),
            _result(0.2, "包含证据一"),
        ],
        "a2": [_result(0.3, "包含证据二")],
        "u1": [],
    }

    result = build_candidate_result(
        candidate_k=20,
        questions=QUESTIONS,
        retrievals=retrievals,
        timings_s=[0.1, 0.2, 0.3],
    )

    assert result["hit_rate"] == 1.0
    assert result["recall"] == 0.75
    assert result["mrr"] == 0.75
    assert result["unanswerable_llm_entry_rate"] == 0.0
    assert result["latency_s"] == {"mean": 0.2, "p50": 0.2, "p95": 0.3}
    assert result["by_category"]["multi_doc"]["recall"] == 0.5
    assert result["per_question"][0]["first_match_rank"] == 2


def test_sweep_recommends_smallest_fast_candidate_on_best_quality_plateau():
    candidate_15 = {
        "candidate_k": 15,
        "hit_rate": 0.9,
        "recall": 0.8,
        "mrr": 0.7,
        "latency_s": {"mean": 0.4, "p50": 0.4, "p95": 0.5},
    }
    candidate_20 = {
        "candidate_k": 20,
        "hit_rate": 1.0,
        "recall": 0.9,
        "mrr": 0.8,
        "latency_s": {"mean": 0.6, "p50": 0.6, "p95": 0.8},
    }
    candidate_30 = {
        "candidate_k": 30,
        "hit_rate": 1.0,
        "recall": 0.9,
        "mrr": 0.8,
        "latency_s": {"mean": 0.9, "p50": 0.9, "p95": 1.2},
    }

    payload = build_retrieval_sweep(
        label="unit-reranker-sweep",
        config={"reranker_model": "BAAI/bge-reranker-v2-m3"},
        ingest={"total_chunks": 3, "ingest_s": 0.1},
        candidates=[candidate_15, candidate_20, candidate_30],
        dataset_version=1,
    )

    assert payload["recommended_candidate_k"] == 20


def test_retrieval_sweep_report_contains_tradeoff_and_failure_ids():
    result = build_candidate_result(
        candidate_k=15,
        questions=QUESTIONS,
        retrievals={
            "a1": [],
            "a2": [_result(0.3, "包含证据二", reranker_score=2.0)],
            "u1": [_result(0.4, "无关内容", reranker_score=-1.0)],
        },
        timings_s=[0.1, 0.2, 0.3],
    )
    payload = build_retrieval_sweep(
        label="report-reranker-sweep",
        config={"reranker_model": "BAAI/bge-reranker-v2-m3"},
        ingest={"total_chunks": 3, "ingest_s": 0.1},
        candidates=[result],
        dataset_version=1,
    )

    report = render_retrieval_sweep(payload)

    assert "| 候选数 | Hit Rate | Recall | MRR |" in report
    assert "推荐候选数：`15`" in report
    assert "检索失败题：a1" in report
    assert "不可回答题进入 LLM：u1" in report


def test_retrieval_sweep_cli_reuses_one_ingest_and_writes_reports(
    monkeypatch, tmp_path
):
    original_enabled = settings.RERANKER_ENABLED
    original_candidate_k = settings.RERANKER_CANDIDATE_K
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    dataset_path = tmp_path / "questions.json"
    dataset_path.write_text(
        json.dumps({"version": 1, "questions": [QUESTIONS[0], QUESTIONS[2]]}, ensure_ascii=False),
        encoding="utf-8",
    )
    ingest_calls = []
    retrieval_calls = []

    monkeypatch.setattr(eval_cli, "_setup_env", lambda args: run_dir)
    monkeypatch.setattr(adapters, "assert_isolation", lambda: None)
    monkeypatch.setattr(adapters, "parse_corpus", lambda corpus: {"doc.txt": "证据一"})
    monkeypatch.setattr(eval_cli, "_validate_dataset", lambda dataset, texts: None)

    def fake_ingest(corpus):
        ingest_calls.append(corpus)
        return {"documents": {"doc.txt": 1}, "total_chunks": 1, "ingest_s": 0.1}

    def fake_retrieve(question):
        retrieval_calls.append((settings.RERANKER_CANDIDATE_K, question))
        if "证据" in question and settings.RERANKER_CANDIDATE_K >= 20:
            rows = [_result(0.3, "包含证据一", reranker_score=1.0)]
        else:
            rows = []
        return {"retrieval": rows, "retrieval_s": settings.RERANKER_CANDIDATE_K / 100}

    monkeypatch.setattr(adapters, "ingest_corpus", fake_ingest)
    monkeypatch.setattr(adapters, "retrieve_only", fake_retrieve, raising=False)
    monkeypatch.setattr(
        adapters,
        "get_run_config",
        lambda: {"reranker_model": "BAAI/bge-reranker-v2-m3"},
    )
    args = SimpleNamespace(
        label="cli-reranker-sweep",
        dataset=str(dataset_path),
        corpus=str(tmp_path),
        candidate_ks=[15, 20, 30],
    )

    eval_cli.cmd_retrieval_sweep(args)

    payload = json.loads((run_dir / "retrieval-sweep.json").read_text("utf-8"))
    assert payload["recommended_candidate_k"] == 20
    assert len(ingest_calls) == 1
    assert [candidate for candidate, _ in retrieval_calls] == [15, 15, 20, 20, 30, 30]
    assert (run_dir / "retrieval-sweep.md").exists()
    assert (run_dir / "retrieval-k15.jsonl").exists()
    assert (run_dir / "retrieval-k20.jsonl").exists()
    assert (run_dir / "retrieval-k30.jsonl").exists()
    assert settings.RERANKER_ENABLED is original_enabled
    assert settings.RERANKER_CANDIDATE_K == original_candidate_k
