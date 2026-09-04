from __future__ import annotations

from copy import deepcopy

from eval.crud_rag_1000.v3_1 import run_phase_d_rerank_context_sweep as phase_d


def _candidate(**overrides):
    result = {
        "document_hit_rate": 0.98,
        "document_recall": 0.95,
        "document_mrr": 0.93,
        "all_gold_docs_recalled_rate": 0.93,
        "multi_document_complete_recall_rate": 0.90,
        "evidence_coverage": 0.95,
    }
    result.update(overrides)
    return result


def _comparison(*, regressed=None, mixed=None):
    return {
        "answerable_regressed_ids": regressed or [],
        "answerable_mixed_ids": mixed or [],
    }


def test_real_config_grid_is_unique_and_never_starves_reranker():
    assert len(set(phase_d.REAL_CONFIG_SPECS)) == len(phase_d.REAL_CONFIG_SPECS)
    for dense_k, reranker_k in phase_d.REAL_CONFIG_SPECS:
        assert dense_k >= reranker_k
        config = phase_d._dense_reranker_config(dense_k, reranker_k)
        assert config["mode"] == "dense"
        assert config["dense_candidate_k"] == dense_k
        assert config["reranker_candidate_k"] == reranker_k
        assert config["final_top_k"] == 5
        assert config["max_chunks_per_document"] == 3


def test_limit_per_document_matches_business_order_and_uses_document_id():
    rows = [
        {"chunk_id": "a1", "metadata": {"document_id": "a", "document_name": "same"}},
        {"chunk_id": "a2", "metadata": {"document_id": "a", "document_name": "same"}},
        {"chunk_id": "b1", "metadata": {"document_id": "b", "document_name": "same"}},
        {"chunk_id": "c1", "metadata": {"document_id": "c", "document_name": "c"}},
    ]
    selected = phase_d._limit_per_document(rows, top_k=3, max_per_document=1)
    assert [row["chunk_id"] for row in selected] == ["a1", "b1", "c1"]


def test_hydrate_reranker_stage_preserves_order_scores_and_full_metadata():
    row = {
        "retrieval": [],
        "trace": {"stages": [{"name": "reranker", "candidates": [
            {"chunk_id": "b", "reranker_score": 2.0},
            {"chunk_id": "a", "reranker_score": 1.0},
        ]}]},
    }
    chunks = {
        "a": {"chunk_id": "a", "content": "A", "metadata": {"document_id": "da", "start_char": 0}},
        "b": {"chunk_id": "b", "content": "B", "metadata": {"document_id": "db", "start_char": 10}},
    }
    hydrated = phase_d._hydrate_reranker_stage(row, chunks)
    assert [item["chunk_id"] for item in hydrated] == ["b", "a"]
    assert hydrated[0]["reranker_score"] == 2.0
    assert hydrated[1]["metadata"]["start_char"] == 0


def test_validate_trace_rejects_replay_mismatch():
    config = phase_d._dense_reranker_config(5, 5, final_k=2, doc_cap=1)
    dense = [
        {"chunk_id": key, "rank": index, "dense_rank": index, "distance": 0.1, "eligible": True, "filtered_reason": None}
        for index, key in enumerate(("a1", "a2", "b1", "c1", "d1"), 1)
    ]
    reranker = [{**row, "reranker_score": 6 - index} for index, row in enumerate(dense, 1)]
    row = {
        "id": "q1",
        "retrieval": [
            {"chunk_id": "a1", "metadata": {"document_id": "a"}},
            {"chunk_id": "a2", "metadata": {"document_id": "a"}},
        ],
        "trace": {
            "retrieval_mode": "dense",
            "reranker_enabled": True,
            "llm": None,
            "timings_ms": {"query_embedding": 1, "dense_retrieval": 1, "rerank": 1, "total": 3},
            "stages": [
                {"name": "dense", "candidates": dense},
                {"name": "reranker", "candidates": reranker},
                {"name": "selected", "candidates": reranker[:2]},
            ],
        },
    }
    chunks = {
        key: {"chunk_id": key, "content": key, "metadata": {"document_id": key[0], "document_name": key[0]}}
        for key in ("a1", "a2", "b1", "c1", "d1")
    }
    try:
        phase_d._validate_trace(row, config, chunks)
    except RuntimeError as exc:
        assert "配额重放" in str(exc)
    else:
        raise AssertionError("应拒绝与业务配额不一致的selected")


def test_reranker_selection_prefers_smaller_budget_on_quality_tie():
    rows = []
    for dense_k, reranker_k in ((15, 15), (20, 20), (50, 50)):
        rows.append({
            "configuration": f"d{dense_k}-r{reranker_k}",
            "dense_k": dense_k,
            "reranker_k": reranker_k,
            "candidate_result": _candidate(),
            "quality_gate": {"passed": True},
        })
    selection = phase_d._select_reranker(rows)
    assert selection["selected_dense_k"] == 15
    assert selection["selected_reranker_k"] == 15


def test_context_selection_prefers_quality_then_smaller_context_on_tie():
    baseline = _candidate()
    rows = [
        {
            "configuration": "top5-doccap3",
            "final_top_k": 5,
            "doc_cap": 3,
            "candidate_result": baseline,
            "quality_gate": {"passed": True},
            "context_budget": {"ingest_token_count": {"mean": 500}, "content_char_count": {"mean": 1000}},
        },
        {
            "configuration": "top8-doccap3",
            "final_top_k": 8,
            "doc_cap": 3,
            "candidate_result": _candidate(document_recall=0.96),
            "quality_gate": {"passed": True},
            "context_budget": {"ingest_token_count": {"mean": 800}, "content_char_count": {"mean": 1600}},
        },
        {
            "configuration": "top5-doccap2",
            "final_top_k": 5,
            "doc_cap": 2,
            "candidate_result": baseline,
            "quality_gate": {"passed": True},
            "context_budget": {"ingest_token_count": {"mean": 450}, "content_char_count": {"mean": 900}},
        },
    ]
    selection = phase_d._select_context(rows, baseline)
    assert selection["selected_quality_configuration"] == "top8-doccap3"
    assert selection["selected_cost_balanced_configuration"] == "top5-doccap2"
    assert selection["quality_improved_over_top5_baseline"] is True


def test_context_budget_uses_stored_chunk_counts():
    rows = [{
        "id": "q1",
        "retrieval": [
            {"content": "abc", "metadata": {"document_id": "a", "char_count": 10, "token_count": 4}},
            {"content": "def", "metadata": {"document_id": "b", "char_count": 20, "token_count": 7}},
        ],
    }]
    budget = phase_d._context_budget(rows)
    assert budget["chunk_count"]["mean"] == 2
    assert budget["distinct_document_count"]["mean"] == 2
    assert budget["content_char_count"]["sum"] == 30
    assert budget["ingest_token_count"]["sum"] == 11


def test_quality_gate_rejects_per_question_regression():
    gate = phase_d._quality_gate(
        _candidate(), _candidate(), _comparison(regressed=["q1"])
    )
    assert gate["passed"] is False
