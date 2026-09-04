from __future__ import annotations

from copy import deepcopy

from eval.crud_rag_1000.v3_1 import run_phase_e_evidence_gate as phase_e


def _features(**overrides):
    result = {
        "reranker_top1": -1.0,
        "reranker_top2": -2.0,
        "reranker_top3": -3.0,
        "reranker_margin_1_2": 1.0,
        "reranker_top3_mean": -2.0,
        "top5_score_ge_0_count": 0,
        "top5_score_ge_m2_count": 2,
        "dense_best_distance": 0.49,
        "reranker_top1_dense_distance": 0.51,
        "reranker_top1_dense_rank": 2,
    }
    result.update(overrides)
    return result


def _multi(name="test", **overrides):
    values = {
        "low_score": -2.5,
        "high_score": 0.5,
        "dense_best_weak": 0.52,
        "top1_dense_weak": 0.55,
    }
    values.update(overrides)
    return phase_e._multi_policy(name, **values)


def test_policy_matrix_is_exact_unique_and_pre_registered():
    assert len(phase_e.POLICY_SPECS) == 16
    assert len({policy["name"] for policy in phase_e.POLICY_SPECS}) == 16
    assert sum(policy["kind"] == "off" for policy in phase_e.POLICY_SPECS) == 1
    assert sum(policy["kind"] == "single_score" for policy in phase_e.POLICY_SPECS) == 7
    assert sum(policy["kind"] == "multi_signal" for policy in phase_e.POLICY_SPECS) == 8
    assert [policy["name"] for policy in phase_e.POLICY_SPECS[-8:]] == [
        f"M{index}" for index in range(1, 9)
    ]


def test_multi_signal_decision_has_low_high_and_quality_first_gray_paths():
    policy = _multi()
    assert phase_e._decision(policy, _features(reranker_top1=-3.0)) == {
        "decision": "reject", "reason": "reranker_top1_below_low"
    }
    assert phase_e._decision(policy, _features(reranker_top1=1.0)) == {
        "decision": "allow", "reason": "reranker_top1_at_or_above_high"
    }
    assert phase_e._decision(policy, _features(
        dense_best_distance=0.53, reranker_top1_dense_distance=0.56
    )) == {"decision": "reject", "reason": "gray_both_dense_signals_weak"}
    assert phase_e._decision(policy, _features(
        dense_best_distance=0.53, reranker_top1_dense_distance=0.54
    )) == {"decision": "allow", "reason": "gray_quality_first_allow"}


def test_threshold_equality_is_not_rejected_and_dense_requires_both_weak():
    policy = _multi()
    assert phase_e._decision(policy, _features(reranker_top1=-2.5))["decision"] == "allow"
    assert phase_e._decision(policy, _features(
        dense_best_distance=0.52, reranker_top1_dense_distance=0.60
    ))["decision"] == "allow"
    assert phase_e._decision(policy, _features(
        dense_best_distance=0.60, reranker_top1_dense_distance=0.55
    ))["decision"] == "allow"


def test_single_score_policy_is_a_control_not_probability_conversion():
    policy = phase_e._single_policy(-2.5)
    assert phase_e._decision(policy, _features(reranker_top1=-2.6))["decision"] == "reject"
    assert phase_e._decision(policy, _features(reranker_top1=-2.5))["decision"] == "allow"
    assert policy["parameters"] == {"score_threshold": -2.5}


def test_perturbation_detects_fragile_distance_boundary():
    policy = _multi(dense_best_weak=0.48, top1_dense_weak=0.50)
    fragile = _features(
        reranker_top1=0.13,
        dense_best_distance=0.48245,
        reranker_top1_dense_distance=0.5067,
    )
    result = phase_e._decision_robustness(policy, fragile)
    assert result["decision"] == "reject"
    assert result["perturbation_stable"] is False
    assert result["perturbed_decisions"] == ["allow", "reject"]


def test_policy_evaluation_uses_explicit_confusion_names_and_hard_gates():
    questions = [
        {"id": "a1", "answerable": True},
        {"id": "u1", "answerable": False},
        {"id": "u2", "answerable": False},
    ]
    features = {
        "a1": _features(reranker_top1=2.0),
        "u1": _features(reranker_top1=-4.0),
        "u2": _features(reranker_top1=-3.0),
    }
    evaluation = phase_e._evaluate_policy(_multi(), questions, features)
    assert evaluation["counts"] == {
        "allowed_answerable": 1,
        "false_rejected_answerable": 0,
        "blocked_unanswerable": 2,
        "missed_unanswerable": 0,
    }
    assert evaluation["strict_eligible"] is False  # 固定正式门槛仍是9道，不随样例缩放


def test_selection_excludes_unstable_and_false_reject_then_prefers_conservative_tie():
    base = {
        "kind": "multi_signal",
        "signal_count": 3,
        "counts": {"blocked_unanswerable": 11, "false_rejected_answerable": 0},
        "strict_eligible": True,
    }
    aggressive = {
        **deepcopy(base),
        "name": "M8",
        "parameters": {
            "low_score": -2.5, "high_score": 0.5,
            "dense_best_weak": 0.48, "top1_dense_weak": 0.50,
        },
        "strict_eligible": False,
    }
    conservative = {
        **deepcopy(base),
        "name": "M2",
        "parameters": {
            "low_score": -2.5, "high_score": 0.5,
            "dense_best_weak": 0.52, "top1_dense_weak": 0.55,
        },
    }
    stricter = {
        **deepcopy(base),
        "name": "M6",
        "parameters": {
            "low_score": -2.5, "high_score": 0.5,
            "dense_best_weak": 0.50, "top1_dense_weak": 0.52,
        },
    }
    selection = phase_e._select_policy([aggressive, stricter, conservative])
    assert selection["selected_policy"] == "M2"
    assert selection["not_a_weighted_score"] is True


def test_apply_gate_never_mutates_source_rows():
    source = [
        {"id": "a", "retrieval": [{"chunk_id": "a1"}]},
        {"id": "u", "retrieval": [{"chunk_id": "u1"}]},
    ]
    original = deepcopy(source)
    gated = phase_e._apply_gate(source, {
        "a": {"decision": "allow"},
        "u": {"decision": "reject"},
    })
    assert source == original
    assert gated[0]["retrieval"] == [{"chunk_id": "a1"}]
    assert gated[1]["retrieval"] == []


def test_extract_features_uses_eligible_dense_and_reranker_source_distance():
    dense = [
        {"distance": 0.20, "eligible": False},
        {"distance": 0.30, "eligible": True},
        {"distance": 0.40, "eligible": True},
    ]
    reranker = [
        {"reranker_score": score, "distance": 0.45 + index / 100, "dense_rank": index + 1}
        for index, score in enumerate((3.0, 1.0, -1.0, -2.0, -3.0))
    ]
    row = {"id": "q", "trace": {"stages": [
        {"name": "dense", "candidates": dense},
        {"name": "reranker", "candidates": reranker},
    ]}}
    features = phase_e._extract_features(row)
    assert features["dense_best_distance"] == 0.30
    assert features["reranker_top1_dense_distance"] == 0.45
    assert features["reranker_margin_1_2"] == 2.0
    assert features["top5_score_ge_0_count"] == 2


def test_parser_requires_explicit_command_run_id_and_verification_evidence():
    parser = phase_e.build_parser()
    run_args = parser.parse_args(["run", "--run-id", "e-test"])
    assert run_args.command == "run"
    assert run_args.phase_d_run_id == phase_e.DEFAULT_PHASE_D_RUN_ID
    verify_args = parser.parse_args([
        "verify", "--run-id", "e-test", "--junit", "tests.xml",
        "--expected-tests", "10",
    ])
    assert verify_args.command == "verify"
    assert verify_args.expected_tests == 10


def test_storage_facts_use_validate_experiment_storage_contract():
    facts = phase_e._storage_facts({
        "database_checks": {
            "metadata.db": {
                "quick_check": "ok", "documents": 1000, "document_units": 1000,
            },
            "chroma.sqlite3": {"quick_check": "ok", "embeddings": 2914},
        }
    })
    assert facts == {
        "database_quick_check": "ok",
        "chroma_quick_check": "ok",
        "documents": 1000,
        "document_units": 1000,
        "embeddings": 2914,
    }
