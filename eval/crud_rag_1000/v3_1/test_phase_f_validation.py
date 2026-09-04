from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from eval.crud_rag_1000.v3_1 import run_phase_f_validation as phase_f
from eval.crud_rag_1000.v3_1 import tuning_runner as runner


def _candidate_quality(**overrides):
    payload = {
        "document_hit_rate": 1.0,
        "document_recall": 0.95,
        "document_mrr": 0.9,
        "all_gold_docs_recalled_rate": 0.95,
        "multi_document_complete_recall_rate": 0.9,
        "evidence_coverage": 0.95,
        "exact_evidence_containment_rate": 0.8,
        "unanswerable_llm_entry_rate": 1.0,
        "per_question": [
            {
                "id": f"q-{index}",
                "answerable": True,
                "all_gold_docs_recalled": index < 19,
            }
            for index in range(20)
        ],
    }
    payload.update(overrides)
    return payload


def _comparison(*, regressed=None, mixed=None):
    return {
        "answerable_regressed_ids": list(regressed or []),
        "answerable_mixed_ids": list(mixed or []),
    }


def _eligible_context(name: str, quality: dict | None = None, *, eligible=True):
    return {
        "configuration": name,
        "eligible": eligible,
        "quality_before_gate_full": quality or _candidate_quality(),
    }


def test_candidate_config_is_frozen_dense20_reranker20_top10_doccap2():
    config = phase_f._candidate_config()
    assert config["mode"] == "dense"
    assert config["dense_candidate_k"] == 20
    assert config["dense_distance_threshold"] == pytest.approx(0.67)
    assert config["reranker_candidate_k"] == 20
    assert config["final_top_k"] == 10
    assert config["max_chunks_per_document"] == 2
    assert config["refusal_gate"]["enabled"] is False
    assert config["refusal_gate"]["available"] is False


def test_original_baseline_is_unchanged_hybrid15_top5_doccap3():
    config = phase_f._original_baseline_config()
    assert config == runner._validated_config_payload(deepcopy(runner.BASELINE_CONFIG))
    assert config["mode"] == "hybrid"
    assert config["dense_candidate_k"] == 15
    assert config["reranker_candidate_k"] == 15
    assert config["final_top_k"] == 5
    assert config["max_chunks_per_document"] == 3


def test_m2_policy_is_exactly_the_e_selection():
    policy = phase_f._m2_policy()
    assert policy["name"] == "M2"
    assert policy["kind"] == "multi_signal"
    assert policy["parameters"] == phase_f.M2_PARAMETERS


@pytest.mark.parametrize(
    ("features", "expected"),
    [
        ({"reranker_top1": -2.51, "dense_best_distance": 0.1, "reranker_top1_dense_distance": 0.1}, "reject"),
        ({"reranker_top1": 0.5, "dense_best_distance": 0.9, "reranker_top1_dense_distance": 0.9}, "allow"),
        ({"reranker_top1": -1.0, "dense_best_distance": 0.53, "reranker_top1_dense_distance": 0.56}, "reject"),
        ({"reranker_top1": -1.0, "dense_best_distance": 0.52, "reranker_top1_dense_distance": 0.56}, "allow"),
    ],
)
def test_m2_decision_boundaries_are_not_retuned(features, expected):
    from eval.crud_rag_1000.v3_1 import run_phase_e_evidence_gate as phase_e

    assert phase_e._decision(phase_f._m2_policy(), features)["decision"] == expected


def test_gate_checks_require_zero_false_reject_three_blocks_and_stability():
    evaluation = {
        "counts": {"false_rejected_answerable": 0, "blocked_unanswerable": 3},
        "robustness": {"unstable_question_count": 0},
    }
    assert phase_f._gate_checks(evaluation)["passed"] is True
    evaluation["counts"]["false_rejected_answerable"] = 1
    assert phase_f._gate_checks(evaluation)["passed"] is False
    evaluation["counts"]["false_rejected_answerable"] = 0
    evaluation["counts"]["blocked_unanswerable"] = 2
    assert phase_f._gate_checks(evaluation)["passed"] is False
    evaluation["counts"]["blocked_unanswerable"] = 3
    evaluation["robustness"]["unstable_question_count"] = 1
    assert phase_f._gate_checks(evaluation)["passed"] is False


def test_hard_quality_checks_require_19_all_gold_and_no_question_loss():
    baseline = _candidate_quality(document_recall=0.9)
    candidate = _candidate_quality(document_recall=0.95)
    result = phase_f._hard_quality_checks(baseline, candidate, _comparison())
    assert result["passed"] is True
    assert result["all_gold_count"] == 19

    candidate["per_question"][18]["all_gold_docs_recalled"] = False
    assert phase_f._hard_quality_checks(baseline, candidate, _comparison())["passed"] is False
    candidate["per_question"][18]["all_gold_docs_recalled"] = True
    assert phase_f._hard_quality_checks(
        baseline, candidate, _comparison(regressed=["q-1"])
    )["passed"] is False


def test_quality_first_selection_chooses_top10_only_on_strict_no_loss_gain():
    top5 = _candidate_quality(document_recall=0.95)
    top10 = _candidate_quality(document_recall=0.96)
    result = phase_f._select_candidate([
        _eligible_context("top10-doccap2", top10),
        _eligible_context("top5-doccap2", top5),
    ])
    assert result["selected_configuration"] == "top10-doccap2"
    assert result["decision"] == "candidate_selected_for_g"


def test_quality_tie_prefers_top5():
    quality = _candidate_quality()
    result = phase_f._select_candidate([
        _eligible_context("top10-doccap2", deepcopy(quality)),
        _eligible_context("top5-doccap2", deepcopy(quality)),
    ])
    assert result["selected_configuration"] == "top5-doccap2"
    assert result["reason"] == "quality_tie_or_mixed_result_prefers_smaller_context"


def test_mixed_metric_result_prefers_top5_instead_of_weighting():
    top5 = _candidate_quality(document_recall=0.95, document_mrr=0.90)
    top10 = _candidate_quality(document_recall=0.96, document_mrr=0.89)
    result = phase_f._select_candidate([
        _eligible_context("top10-doccap2", top10),
        _eligible_context("top5-doccap2", top5),
    ])
    assert result["selected_configuration"] == "top5-doccap2"


def test_only_eligible_candidate_is_selected():
    result = phase_f._select_candidate([
        _eligible_context("top10-doccap2", eligible=False),
        _eligible_context("top5-doccap2", eligible=True),
    ])
    assert result["selected_configuration"] == "top5-doccap2"
    assert result["reason"] == "only_one_candidate_passed_frozen_validation_gates"


def test_no_eligible_candidate_yields_not_adopted_without_retuning():
    result = phase_f._select_candidate([
        _eligible_context("top10-doccap2", eligible=False),
        _eligible_context("top5-doccap2", eligible=False),
    ])
    assert result["decision"] == "not_adopted"
    assert result["selected_configuration"] is None
    assert "no threshold" in result["selection_rule"]


def test_public_context_removes_full_per_question_payloads():
    context = {
        "configuration": "top5-doccap2",
        "quality_before_gate_full": {"per_question": [1]},
        "quality_after_gate_full": {"per_question": [2]},
        "rows_before_gate": [1],
        "rows_after_gate": [2],
        "eligible": True,
    }
    public = phase_f._public_context(context)
    assert public == {"configuration": "top5-doccap2", "eligible": True}


def test_validation_split_is_fixed_24_with_20_answerable_and_4_unanswerable():
    questions = runner._split_questions(runner._dataset(), "validation")
    assert len(questions) == 24
    assert len({row["id"] for row in questions}) == 24
    assert sum(bool(row["answerable"]) for row in questions) == 20
    assert sum(not bool(row["answerable"]) for row in questions) == 4


def test_f_source_only_requests_validation_not_frozen_test():
    source = Path(phase_f.__file__).read_text(encoding="utf-8")
    assert 'runner._split_questions(dataset, "validation")' in source
    assert 'runner._split_questions(dataset, "frozen_test")' not in source


def test_e_contract_is_passed_and_m2_is_frozen():
    experiment, _ = runner._load_protocol(phase_f.DEFAULT_EXPERIMENT_ID)
    contract = phase_f._validate_e_contract(experiment, phase_f.DEFAULT_PHASE_E_RUN_ID)
    assert contract["selection"]["selected_policy"] == "M2"
    assert contract["selection"]["selected_parameters"] == phase_f.M2_PARAMETERS
    assert all(item["bytes"] > 0 for item in contract["identities"].values())


def test_final_verification_uses_nested_storage_contract():
    from eval.crud_rag_1000.v3_1 import run_phase_e_evidence_gate as phase_e

    storage = {
        "database_checks": {
            "metadata.db": {
                "quick_check": "ok",
                "documents": 1000,
                "document_units": 1000,
            },
            "chroma.sqlite3": {"quick_check": "ok", "embeddings": 2914},
        }
    }
    assert phase_e._storage_facts(storage) == {
        "database_quick_check": "ok",
        "chroma_quick_check": "ok",
        "documents": 1000,
        "document_units": 1000,
        "embeddings": 2914,
    }
