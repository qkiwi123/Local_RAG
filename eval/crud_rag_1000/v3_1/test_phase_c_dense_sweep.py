from __future__ import annotations

from copy import deepcopy

from eval.crud_rag_1000.v3_1 import run_phase_c_dense_sweep as phase_c


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


def test_dense_config_changes_only_dense_mode_k_and_distance():
    config = phase_c._dense_config(30, 0.70)
    baseline = deepcopy(phase_c.runner.BASELINE_CONFIG)
    assert config["mode"] == "dense"
    assert config["reranker_enabled"] is True
    assert config["dense_candidate_k"] == 30
    assert config["dense_distance_threshold"] == 0.70
    for key in baseline:
        if key not in {"mode", "reranker_enabled", "dense_candidate_k", "dense_distance_threshold"}:
            assert config[key] == baseline[key]


def test_stage_candidates_split_dense_raw_and_eligible():
    row = {
        "retrieval": [{"chunk_id": "selected"}],
        "trace": {"stages": [{
            "name": "dense",
            "candidates": [
                {"chunk_id": "a", "eligible": True},
                {"chunk_id": "b", "eligible": False},
            ],
        }]},
    }
    assert [item["chunk_id"] for item in phase_c._stage_candidates(row, "dense_raw")] == ["a", "b"]
    assert [item["chunk_id"] for item in phase_c._stage_candidates(row, "dense_eligible")] == ["a"]
    assert [item["chunk_id"] for item in phase_c._stage_candidates(row, "selected")] == ["selected"]


def test_quality_gate_rejects_aggregate_loss():
    baseline = _candidate()
    worse = _candidate(document_recall=0.94)
    gate = phase_c._quality_gate(baseline, worse, _comparison())
    assert gate["passed"] is False
    assert gate["metric_not_below_dense_baseline"]["document_recall"] is False


def test_quality_gate_rejects_per_question_mixed_loss():
    baseline = _candidate()
    gate = phase_c._quality_gate(baseline, _candidate(), _comparison(mixed=["q1"]))
    assert gate["passed"] is False
    assert gate["no_answerable_regression_or_mixed_loss"] is False


def test_select_k_prefers_smaller_k_on_exact_quality_tie():
    rows = []
    for dense_k in phase_c.DENSE_K_VALUES:
        rows.append({
            "configuration": f"k{dense_k}",
            "dense_k": dense_k,
            "candidate_result": _candidate(),
            "quality_gate": {"passed": True},
        })
    assert phase_c._select_k(rows)["selected_dense_k"] == 10


def test_control_stability_uses_eligible_flags():
    before = [{
        "id": "q1",
        "retrieval": [{"chunk_id": "a"}],
        "trace": {"stages": [{"name": "dense", "candidates": [
            {"chunk_id": "a", "eligible": True},
            {"chunk_id": "b", "eligible": False},
        ]}]},
    }]
    after = deepcopy(before)
    after[0]["trace"]["stages"][0]["candidates"][1]["eligible"] = True
    result = phase_c._control_stability(before, after, ("dense_raw", "dense_eligible"))
    assert result["stage_order_identical_count"]["dense_raw"] == 1
    assert result["stage_order_identical_count"]["dense_eligible"] == 0
    assert result["stage_exact_identical_count"]["dense_raw"] == 0
    assert result["passed"] is False


def test_validate_trace_accepts_dense_threshold_and_reranker_contract():
    config = phase_c._dense_config(5, 0.50)
    dense = [
        {"chunk_id": "a", "rank": 1, "dense_rank": 1, "distance": 0.40, "eligible": True, "filtered_reason": None},
        {"chunk_id": "b", "rank": 2, "dense_rank": 2, "distance": 0.45, "eligible": True, "filtered_reason": None},
        {"chunk_id": "c", "rank": 3, "dense_rank": 3, "distance": 0.50, "eligible": True, "filtered_reason": None},
        {"chunk_id": "d", "rank": 4, "dense_rank": 4, "distance": 0.51, "eligible": False, "filtered_reason": "distance_above_threshold"},
        {"chunk_id": "e", "rank": 5, "dense_rank": 5, "distance": 0.60, "eligible": False, "filtered_reason": "distance_above_threshold"},
    ]
    reranker = [
        {**dense[0], "reranker_score": 3.0},
        {**dense[1], "reranker_score": 2.0},
        {**dense[2], "reranker_score": 1.0},
    ]
    row = {
        "id": "q1",
        "retrieval": reranker,
        "trace": {
            "retrieval_mode": "dense",
            "reranker_enabled": True,
            "llm": None,
            "timings_ms": {"query_embedding": 1, "dense_retrieval": 1, "rerank": 1, "total": 3},
            "stages": [
                {"name": "dense", "candidates": dense},
                {"name": "reranker", "candidates": reranker},
                {"name": "selected", "candidates": reranker},
            ],
        },
    }
    phase_c._validate_trace(row, config)
