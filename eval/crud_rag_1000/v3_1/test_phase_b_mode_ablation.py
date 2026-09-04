from __future__ import annotations

from copy import deepcopy

from eval.crud_rag_1000.v3_1 import run_phase_b_mode_ablation as phase_b


def _row(qid: str, chunks: list[str], *, dense: list[str] | None = None):
    candidates = [{"chunk_id": chunk_id} for chunk_id in chunks]
    dense_candidates = [{"chunk_id": chunk_id} for chunk_id in (dense or chunks)]
    return {
        "id": qid,
        "retrieval": candidates,
        "trace": {
            "stages": [
                {"name": "dense", "candidates": dense_candidates},
                {"name": "selected", "candidates": candidates},
            ]
        },
    }


def test_phase_b_has_six_unique_configs_and_changes_only_mode_or_reranker():
    configs = phase_b._phase_b_configs()
    assert set(configs) == {
        "hybrid_reranker",
        "dense_reranker",
        "bm25_reranker",
        "hybrid_no_reranker",
        "dense_no_reranker",
        "bm25_no_reranker",
    }
    assert len({phase_b.runner._config_id(value) for value in configs.values()}) == 6
    for config in configs.values():
        expected = deepcopy(phase_b.runner.BASELINE_CONFIG)
        expected["mode"] = config["mode"]
        expected["reranker_enabled"] = config["reranker_enabled"]
        assert config == expected


def test_control_stability_checks_each_stage_and_selected_order():
    before = [_row("q1", ["a", "b"]), _row("q2", ["c"])]
    stable = phase_b._control_stability(before, deepcopy(before))
    assert stable["passed"] is True
    after = deepcopy(before)
    after[0]["retrieval"] = [{"chunk_id": "b"}, {"chunk_id": "a"}]
    changed = phase_b._control_stability(before, after)
    assert changed["passed"] is False
    assert changed["stage_order_difference_ids"]["selected"] == ["q1"]


def test_parser_requires_run_id_and_keeps_parent_experiment_default():
    args = phase_b.build_parser().parse_args(["--run-id", "b-fixture"])
    assert args.run_id == "b-fixture"
    assert args.experiment_id == "v35-a-baseline-20260829"
