"""Unit and artifact checks for the isolated unanswerable retrieval audit."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "audit_unanswerable_retrieval.py"
OUTPUT = ROOT / "v3" / "retrieval_top50.json"
AUDIT = ROOT / "v3" / "whole_corpus_retrieval_audit.json"


def _module():
    spec = importlib.util.spec_from_file_location("audit_unanswerable_retrieval", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_compact_and_risk_flags_are_conservative() -> None:
    module = _module()
    rankings = {
        "dense": [
            {
                "rank": 1,
                "chunk_id": "c1",
                "document_name": "doc.txt",
                "content": "杭州的救援物资包括饮用水100箱，并已送达。",
            }
        ],
        "bm25": [],
        "hybrid": [],
    }
    result = module._build_risk_summary(
        question="杭州送去了哪些救援物资？",
        answer="饮用水100箱",
        key_facts=["100"],
        gold_documents=[],
        rankings=rankings,
    )
    assert result["automatic_result"] == "needs_review"
    assert result["automatic_risk_flag_count"] >= 1
    assert result["requires_human_semantic_review"] is True


def test_safe_replace_scope_rejects_v3_root(tmp_path: Path) -> None:
    module = _module()
    with pytest.raises(ValueError, match="拒绝清理"):
        module._safe_replace_run_dir(module.V3_DIR)


@pytest.mark.skipif(not OUTPUT.exists(), reason="先运行 retrieval audit")
def test_retrieval_artifacts_are_isolated_complete_and_still_draft() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    assert payload["status"] == "automatic_retrieval_complete_human_review_pending"
    assert payload["whole_corpus_checked"] is False
    assert payload["llm_called"] is False
    assert payload["reranker_called"] is False
    assert payload["database_used"] is True
    assert payload["database_isolation"]["production_database_used"] is False
    assert payload["database_isolation"]["source_frozen_eval_index_opened"] is False
    assert payload["runtime"]["embedding_device"] == "cuda:0"
    assert payload["runtime"]["bm25_device"] == "cpu"
    assert payload["runtime"]["distance_threshold"] is None
    assert payload["runtime"]["reranker_enabled"] is False
    assert payload["index_counts"] == {"chunks": 2914, "documents": 1000}
    assert len(payload["questions"]) == 20
    assert len(audit["questions"]) == 20
    for item in payload["questions"]:
        assert item["id"] != "crud-unanswerable-2docs-64fa9b31b82641eb8ecc06ef"
        assert item["unanswerable_type"] in {"out_of_scope", "insufficient_detail"}
        assert len(item["rankings"]["dense"]) == 50
        assert 1 <= len(item["rankings"]["bm25"]) <= 50
        assert len(item["rankings"]["hybrid"]) == 50
        assert item["review"]["status"] == "draft"
        assert item["review"]["whole_corpus_checked"] is False
    review_files = list((ROOT / "v3" / "retrieval_review").glob("*.md"))
    assert len(review_files) == 20
