from __future__ import annotations

import pytest

from eval.crud_rag_1000.v3_1 import run_phase_b2_hybrid_bm25_sweep as phase_b2


def _candidate(**overrides):
    row = {
        "document_hit_rate": 0.98,
        "document_recall": 0.95,
        "document_mrr": 0.93,
        "all_gold_docs_recalled_rate": 0.93,
        "multi_document_complete_recall_rate": 0.90,
        "evidence_coverage": 0.95,
    }
    row.update(overrides)
    return row


def _evaluation(name, value, *, passed=True, candidate=None):
    candidate = candidate or _candidate()
    return {
        "configuration": name,
        "bm25_k": 15,
        "k1": 1.5,
        "b": 0.75,
        "rrf_k": 60,
        "quality_gate": {"passed": passed},
        "candidate_result": candidate,
        "value": value,
    }


def test_coordinate_grids_are_unique_and_include_defaults():
    for values, default in (
        (phase_b2.BM25_K_VALUES, phase_b2.DEFAULT_BM25_K),
        (phase_b2.K1_VALUES, phase_b2.DEFAULT_K1),
        (phase_b2.B_VALUES, phase_b2.DEFAULT_B),
        (phase_b2.RRF_VALUES, phase_b2.DEFAULT_RRF_K),
    ):
        assert len(values) == len(set(values))
        assert default in values


def test_b2_identity_covers_sparse_fusion_reranker_and_trace_code():
    identity = phase_b2._b2_code_identity()
    assert "backend/services/sparse_retriever.py" in identity
    assert "backend/services/fusion.py" in identity
    assert "backend/services/reranker.py" in identity
    assert "backend/services/retrieval_trace.py" in identity
    assert "eval/crud_rag_1000/v3_1/run_phase_b2_hybrid_bm25_sweep.py" in identity


def test_hybrid_config_freezes_d_selected_dense_and_reranker_budget():
    config = phase_b2._hybrid_config(bm25_k=30, k1=1.2, b=0.5, rrf_k=40)
    assert config["mode"] == "hybrid"
    assert config["dense_candidate_k"] == 20
    assert config["dense_distance_threshold"] == 0.67
    assert config["reranker_candidate_k"] == 20
    assert config["final_top_k"] == 5
    assert config["max_chunks_per_document"] == 3
    assert config["bm25_top_k"] == 30
    assert config["bm25_k1"] == 1.2
    assert config["bm25_b"] == 0.5
    assert config["rrf_k"] == 40


def test_config_name_is_stable_and_distinguishes_coordinates():
    left = phase_b2._config_name(phase_b2._hybrid_config(k1=0.8, b=0.25))
    right = phase_b2._config_name(phase_b2._hybrid_config(k1=1.2, b=0.25))
    assert left == "hybrid-bm15-k1080-b025-rrf60"
    assert left != right


def test_hybrid_trace_guard_recomputes_rrf_from_dense_and_bm25(monkeypatch):
    from backend.services.fusion import reciprocal_rank_fusion

    monkeypatch.setattr(phase_b2.phase_c, "_validate_trace", lambda *_: None)
    dense = [
        {"chunk_id": "a", "rank": 1, "dense_rank": 1, "distance": 0.1, "eligible": True},
        {"chunk_id": "b", "rank": 2, "dense_rank": 2, "distance": 0.2, "eligible": True},
    ]
    bm25 = [
        {"chunk_id": "b", "rank": 1, "bm25_rank": 1, "bm25_score": 3.0},
        {"chunk_id": "c", "rank": 2, "bm25_rank": 2, "bm25_score": 2.0},
    ]
    config = phase_b2._hybrid_config()
    rrf = reciprocal_rank_fusion(dense, bm25, rrf_k=60, top_k=20)
    row = {"id": "q1", "trace": {"stages": [
        {"name": "dense", "candidates": dense},
        {"name": "bm25", "candidates": bm25},
        {"name": "rrf", "candidates": rrf},
    ]}}
    phase_b2._validate_hybrid_trace(row, config)
    row["trace"]["stages"][2]["candidates"][0]["rrf_score"] += 0.01
    with pytest.raises(RuntimeError, match="RRF分数"):
        phase_b2._validate_hybrid_trace(row, config)


def test_cross_config_invariants_cover_dense_prefix_rrf_upstream_and_shared_scores():
    dense = [
        {"chunk_id": "a", "rank": 1, "dense_rank": 1, "distance": 0.1, "eligible": True, "filtered_reason": None},
        {"chunk_id": "b", "rank": 2, "dense_rank": 2, "distance": 0.2, "eligible": True, "filtered_reason": None},
    ]
    bm25_short = [
        {"chunk_id": "b", "rank": 1, "bm25_rank": 1, "bm25_score": 3.0, "distance": None},
    ]
    bm25_long = [
        *bm25_short,
        {"chunk_id": "c", "rank": 2, "bm25_rank": 2, "bm25_score": 2.0, "distance": None},
    ]

    def row(bm25, scores, *, selected=None):
        selected = list(scores)[:2] if selected is None else selected
        return {"id": "q1", "retrieval": [
            {"chunk_id": key} for key in selected
        ], "trace": {"stages": [
            {"name": "dense", "candidates": [dict(item) for item in dense]},
            {"name": "bm25", "candidates": [dict(item) for item in bm25]},
            {"name": "reranker", "candidates": [
                {"chunk_id": key, "reranker_score": score}
                for key, score in scores.items()
            ]},
            {"name": "selected", "candidates": [
                {"chunk_id": key} for key in selected
            ]},
        ]}}

    control = [row([], {})]
    pass_rows = {
        "p10": [row(bm25_short, {"a": 1.0, "b": 0.5})],
        "p20": [row(bm25_long, {"a": 1.0, "b": 0.5, "c": 0.2})],
    }
    evaluations = [
        {"configuration": "k1", "config_id": "c1", "pass": "p10", "bm25_k": 1},
        {"configuration": "k2", "config_id": "c2", "pass": "p20", "bm25_k": 2},
    ]
    result = phase_b2._cross_config_invariants(
        dense_control_rows=control,
        evaluations=evaluations,
        pass_rows=pass_rows,
        topk_evaluations=evaluations,
        rrf_evaluations=[evaluations[1]],
    )
    assert result["all_hybrid_dense_matches_dense_control"] is True
    assert result["bm25_topk_prefix_pair_count"] == 1
    assert result["reranker_shared_score_comparisons"] == 2
    assert result["reranker_shared_score_max_absolute_delta"] == 0.0
    assert result["same_reranker_candidate_set_selected_top5_exact"] is True

    pass_rows["p20"][0]["trace"]["stages"][0]["candidates"][0]["distance"] = 0.11
    with pytest.raises(RuntimeError, match="Dense分支"):
        phase_b2._cross_config_invariants(
            dense_control_rows=control,
            evaluations=evaluations,
            pass_rows=pass_rows,
            topk_evaluations=evaluations,
            rrf_evaluations=[evaluations[1]],
        )


def test_cross_config_invariants_allow_bounded_fp16_score_drift():
    dense = [{
        "chunk_id": "a", "rank": 1, "dense_rank": 1, "distance": 0.1,
        "eligible": True, "filtered_reason": None,
    }]

    def row(scores):
        return {"id": "q1", "retrieval": [
            {"chunk_id": key} for key in scores
        ], "trace": {"stages": [
            {"name": "dense", "candidates": [dict(dense[0])]},
            {"name": "bm25", "candidates": []},
            {"name": "reranker", "candidates": [
                {"chunk_id": key, "reranker_score": score}
                for key, score in scores.items()
            ]},
            {"name": "selected", "candidates": [
                {"chunk_id": key} for key in scores
            ]},
        ]}}

    evaluations = [
        {"configuration": "left", "config_id": "c1", "pass": "p1", "bm25_k": 1},
        {"configuration": "right", "config_id": "c2", "pass": "p2", "bm25_k": 1},
    ]
    result = phase_b2._cross_config_invariants(
        dense_control_rows=[row({})],
        evaluations=evaluations,
        pass_rows={
            "p1": [row({"a": 3.677734375, "b": -2.0})],
            "p2": [row({"a": 3.689453125, "b": -2.0})],
        },
        topk_evaluations=[],
        rrf_evaluations=[],
    )
    assert result["reranker_shared_varying_pair_count"] == 1
    assert result["reranker_shared_score_max_absolute_delta"] == 0.01171875
    assert result["reranker_shared_scores_stable_with_tolerance"] == 0.02

    with pytest.raises(RuntimeError, match="超过FP16容差"):
        phase_b2._cross_config_invariants(
            dense_control_rows=[row({})],
            evaluations=evaluations,
            pass_rows={
                "p1": [row({"a": 3.67, "b": -2.0})],
                "p2": [row({"a": 3.70, "b": -2.0})],
            },
            topk_evaluations=[],
            rrf_evaluations=[],
        )


def test_cross_config_invariants_reject_same_set_top5_change_or_non_tie_reorder():
    dense = [{
        "chunk_id": "a", "rank": 1, "dense_rank": 1, "distance": 0.1,
        "eligible": True, "filtered_reason": None,
    }]

    def row(order, scores, selected):
        return {"id": "q1", "retrieval": [
            {"chunk_id": key} for key in selected
        ], "trace": {"stages": [
            {"name": "dense", "candidates": [dict(dense[0])]},
            {"name": "bm25", "candidates": []},
            {"name": "reranker", "candidates": [
                {"chunk_id": key, "reranker_score": scores[key]} for key in order
            ]},
            {"name": "selected", "candidates": [
                {"chunk_id": key} for key in selected
            ]},
        ]}}

    evaluations = [
        {"configuration": "left", "config_id": "c1", "pass": "p1", "bm25_k": 1},
        {"configuration": "right", "config_id": "c2", "pass": "p2", "bm25_k": 1},
    ]
    kwargs = {
        "dense_control_rows": [row([], {}, [])],
        "evaluations": evaluations,
        "topk_evaluations": [],
        "rrf_evaluations": [],
    }
    with pytest.raises(RuntimeError, match="最终Top5变化"):
        phase_b2._cross_config_invariants(
            **kwargs,
            pass_rows={
                "p1": [row(["a", "b"], {"a": 1.0, "b": 0.5}, ["a"])],
                "p2": [row(["a", "b"], {"a": 1.0, "b": 0.5}, ["b"])],
            },
        )
    with pytest.raises(RuntimeError, match="非同分排序反转"):
        phase_b2._cross_config_invariants(
            **kwargs,
            pass_rows={
                "p1": [row(["a", "b"], {"a": 1.0, "b": 0.5}, ["a"])],
                "p2": [row(["b", "a"], {"a": 1.0, "b": 0.5}, ["a"])],
            },
        )


def test_coordinate_selection_prefers_gate_pass_before_higher_aggregate():
    failed = _evaluation(
        "failed", 10, passed=False,
        candidate=_candidate(document_recall=0.99, all_gold_docs_recalled_rate=0.99),
    )
    passed = _evaluation("passed", 15, passed=True)
    selection = phase_b2._coordinate_select(
        [failed, passed], parameter="value", default_value=15
    )
    assert selection["selected_configuration"] == "passed"
    assert selection["selection_pool"] == "dense_quality_gate_passed"


def test_coordinate_selection_can_continue_when_no_candidate_passes_gate():
    weak = _evaluation("weak", 10, passed=False, candidate=_candidate(document_recall=0.94))
    best = _evaluation("best", 20, passed=False, candidate=_candidate(document_recall=0.96))
    selection = phase_b2._coordinate_select(
        [weak, best], parameter="value", default_value=15
    )
    assert selection["selected_configuration"] == "best"
    assert selection["selection_pool"] == "best_available_no_gate_pass"


def test_final_selection_only_promotes_strict_quality_improvement():
    baseline = _candidate()
    tied = _evaluation("tied", 15, passed=True, candidate=_candidate())
    tied_selection = phase_b2._final_select([tied], baseline)
    assert tied_selection["selected_configuration"] == "tied"
    assert tied_selection["promoted_configuration"] is None
    assert tied_selection["recommendation"] == "retain_dense_d20_r20_quality_tie"

    improved = _evaluation(
        "improved", 20, passed=True,
        candidate=_candidate(document_recall=0.96),
    )
    improved_selection = phase_b2._final_select([tied, improved], baseline)
    assert improved_selection["promoted_configuration"] == "improved"
    assert improved_selection["recommendation"] == "hybrid_candidate_requires_limited_d_recheck"


def test_final_selection_handles_no_quality_safe_hybrid():
    selection = phase_b2._final_select(
        [_evaluation("unsafe", 15, passed=False)], _candidate()
    )
    assert selection["selected_configuration"] is None
    assert selection["recommendation"] == "retain_dense_d20_r20"
