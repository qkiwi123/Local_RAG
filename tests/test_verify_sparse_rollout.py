from copy import deepcopy

import pytest

from scripts.verify_sparse_rollout import PROJECT, compare_runs, digest, validate_root


def pair():
    old = {"status":"passed","backend":"rank_bm25_memory","runtime_retrieval":{},
           "rows":[{"id":str(i),"ordered_sha256":"x","stage_sha256":{"dense":"y"}} for i in range(120)],
           "sparse_top50_hashes":["x"]*120,"warm_sparse_ms":{"p95":10}}
    new = deepcopy(old)
    new["backend"] = "bm25s_mmap"
    return old,new


def test_gate_exact_and_limit():
    old,new = pair()
    new["warm_sparse_ms"]["p95"] = 11
    assert compare_runs(old,new)["passed"]
    new["warm_sparse_ms"]["p95"] = 11.01
    assert not compare_runs(old,new)["passed"]


def test_known_cross_process_stage_drift_is_not_bm25_failure():
    old,new = pair()
    new["rows"][0]["stage_sha256"]["dense"] = "different"
    assert compare_runs(old,new)["passed"]


def test_known_cross_process_final_drift_is_observed_but_not_bm25_failure():
    old,new = pair()
    new["rows"][0]["ordered_sha256"] = "different"
    result = compare_runs(old,new)
    assert result["passed"]
    assert not result["full_chain_order_exact_observation"]


def test_sparse_regression_fails():
    old,new = pair()
    new["sparse_top50_hashes"][0] = "different"
    assert not compare_runs(old,new)["passed"]


@pytest.mark.parametrize("change", ["count","config","status","backend"])
def test_incomparable_runs_rejected(change):
    old,new = pair()
    if change == "count":
        new["rows"].pop()
    elif change == "config":
        new["runtime_retrieval"] = {"different":True}
    elif change == "status":
        new["status"] = "failed"
    else:
        new["backend"] = "rank_bm25_memory"
    with pytest.raises(ValueError):
        compare_runs(old,new)


def test_report_root_is_confined():
    validate_root(PROJECT / "data/backups/test")
    with pytest.raises(ValueError):
        validate_root(PROJECT / "data")


def test_digest_contains_no_raw_content():
    value = digest(["private document text"])
    assert len(value) == 64
    assert "private" not in value
