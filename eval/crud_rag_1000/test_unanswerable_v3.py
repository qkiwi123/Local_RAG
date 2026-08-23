"""Offline acceptance tests for the CRUD_RAG 120-question draft fixture."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from eval.dataset_v3 import parse_corpus, validate_dataset  # noqa: E402
from eval.crud_rag_1000.build_unanswerable_v3 import (  # noqa: E402
    CORPUS_DIR,
    CORPUS_ID,
    DATASET_ID,
    OLD_DATASET_PATH,
    OLD_MANIFEST_PATH,
    V3_DIR,
    category_document_count,
    normalize_text,
)


DATASET_PATH = V3_DIR / "questions_v3_120q.json"
SELECTION_PATH = V3_DIR / "unanswerable_selection_manifest.json"
AUDIT_PATH = V3_DIR / "whole_corpus_audit.json"
PROVENANCE_PATH = V3_DIR / "provenance.json"
OVERRIDES_PATH = V3_DIR / "selection_overrides.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


@pytest.fixture(scope="module")
def dataset() -> dict:
    return load_json(DATASET_PATH)


@pytest.fixture(scope="module")
def selection() -> dict:
    return load_json(SELECTION_PATH)


@pytest.fixture(scope="module")
def audit() -> dict:
    return load_json(AUDIT_PATH)


def test_dataset_and_corpus_counts_and_new_distribution(dataset: dict, selection: dict) -> None:
    assert dataset["dataset_id"] == DATASET_ID
    assert dataset["corpus_id"] == CORPUS_ID
    assert len(dataset["questions"]) == 120
    assert sum(item["answerable"] is True for item in dataset["questions"]) == 100
    assert sum(item["answerable"] is False for item in dataset["questions"]) == 20
    new = [item for item in dataset["questions"] if not item["answerable"]]
    assert {item["source"]["upstream_task"] for item in new} == {
        "questanswer_1doc",
        "questanswer_2docs",
        "questanswer_3docs",
    }
    assert {
        category: sum(item["source"]["upstream_task"] == category for item in new)
        for category in ("questanswer_1doc", "questanswer_2docs", "questanswer_3docs")
    } == {"questanswer_1doc": 7, "questanswer_2docs": 7, "questanswer_3docs": 6}
    assert {
        split: sum(item["split"] == split for item in new)
        for split in ("development", "validation", "frozen_test")
    } == {"development": 12, "validation": 4, "frozen_test": 4}
    assert len(list(CORPUS_DIR.rglob("*.txt"))) == 1000
    assert selection["corpus_exclusion"]["corpus_document_count"] == 1000


def test_old_questions_and_manifest_are_unchanged(dataset: dict, selection: dict) -> None:
    old = load_json(OLD_DATASET_PATH)
    assert dataset["questions"][:100] == old["questions"]
    assert len(old["questions"]) == 100
    assert selection["invariants"]["old_manifest_sha256"] == hashlib.sha256(
        OLD_MANIFEST_PATH.read_bytes()
    ).hexdigest()
    assert load_json(OLD_MANIFEST_PATH)["corpus_id"] == CORPUS_ID


def test_new_question_runtime_fields_and_identity(dataset: dict, selection: dict) -> None:
    new = [item for item in dataset["questions"] if not item["answerable"]]
    selected = selection["selection"]["questions"]
    assert len(selected) == 20
    assert {item["id"] for item in new} == {item["id"] for item in selected}
    assert len({item["id"] for item in dataset["questions"]}) == 120
    assert len({item["question_group"] for item in dataset["questions"]}) == 120
    assert len({item["question"] for item in dataset["questions"]}) == 120
    for item in new:
        assert item["category"] == "unanswerable"
        assert item["unanswerable_type"] in {"out_of_scope", "insufficient_detail"}
        assert item["evidence"] == []
        assert item["keypoints"] == []
        assert item["reference_answer"] == "根据给定语料无法回答该问题。"
        assert item["review"] == {
            "status": "draft",
            "reviewer": None,
            "reviewed_at": None,
            "rubric_version": "rag-gold-v1",
            "evidence_checked": False,
            "answer_checked": False,
            "whole_corpus_checked": False,
        }


def test_selection_upstream_mapping_and_excluded_gold_absent(selection: dict) -> None:
    upstream = load_json(ROOT / "source" / "split_merged.json")
    corpus_norm_hashes = {
        hashlib.sha256(normalize_text(path.read_text(encoding="utf-8")).encode("utf-8")).hexdigest()
        for path in CORPUS_DIR.rglob("*.txt")
    }
    rows = {
        (category, row["ID"]): row
        for category in ("questanswer_1doc", "questanswer_2docs", "questanswer_3docs")
        for row in upstream[category]
    }
    for item in selection["selection"]["questions"]:
        row = rows[(item["category"], item["upstream_id"])]
        assert item["upstream_question"] == row["questions"].strip()
        assert item["upstream_answer"] == row["answers"].strip()
        assert item["auto_screen_pass"] is True
        assert item["rejection_reasons"] == []
        expected_count = category_document_count(item["category"])
        assert len(item["excluded_gold_documents"]) == expected_count
        assert all(
            gold["sha256"] not in corpus_norm_hashes
            for gold in item["excluded_gold_documents"]
        )
        for index, gold in enumerate(item["excluded_gold_documents"], start=1):
            assert gold["news_index"] == index
            assert gold["text"] == normalize_text(row[f"news{index}"])
            assert gold["sha256"] == hashlib.sha256(gold["text"].encode("utf-8")).hexdigest()


def test_whole_corpus_audit_is_draft_and_retrieval_pending(audit: dict) -> None:
    assert audit["status"] == "draft"
    assert audit["whole_corpus_checked"] is False
    assert audit["retrieval"]["status"] == "pending"
    assert audit["retrieval"]["llm_called"] is False
    assert audit["retrieval"]["database_used"] is False
    assert len(audit["questions"]) == 20
    for item in audit["questions"]:
        assert item["whole_corpus_checked"] is False
        assert item["retrieval"]["status"] == "pending"
        assert item["excluded_gold_in_corpus"] is False


def test_non_strict_passes_and_strict_fails_due_to_draft(dataset: dict) -> None:
    parsed = parse_corpus(CORPUS_DIR)
    manifest = load_json(OLD_MANIFEST_PATH)
    nonstrict = validate_dataset(
        dataset,
        parsed_documents=parsed,
        manifest=manifest,
        current_manifest=manifest,
        strict=False,
    )
    assert nonstrict.ok, nonstrict.errors
    strict = validate_dataset(
        dataset,
        parsed_documents=parsed,
        manifest=manifest,
        current_manifest=manifest,
        strict=True,
    )
    assert not strict.ok
    assert any("review.status" in error for error in strict.errors)
    assert any("whole_corpus_checked" in error for error in strict.errors)


def test_review_packets_and_provenance_are_complete(dataset: dict, selection: dict) -> None:
    new_ids = {item["id"] for item in dataset["questions"] if not item["answerable"]}
    review_ids = {path.stem for path in (V3_DIR / "review").glob("*.md")}
    assert review_ids == new_ids
    for path in (V3_DIR / "review").glob("*.md"):
        content = path.read_text(encoding="utf-8")
        assert "status：`draft`" in content
        assert "whole_corpus_checked：`false`" in content
        assert "Dense Top50：`pending`" in content
        assert "BM25 Top50：`pending`" in content
        assert "Hybrid Top50：`pending`" in content
    provenance = load_json(PROVENANCE_PATH)
    assert provenance["inputs"]["old_dataset"]["sha256"] == hashlib.sha256(
        OLD_DATASET_PATH.read_bytes()
    ).hexdigest()
    assert provenance["inputs"]["old_corpus_manifest"]["sha256"] == hashlib.sha256(
        OLD_MANIFEST_PATH.read_bytes()
    ).hexdigest()
    assert provenance["deterministic"] is True
    assert provenance["network_used"] is False
    assert provenance["llm_used"] is False
    assert provenance["database_used"] is False
    assert provenance["review_policy"]["status"] == "draft"
    assert len(provenance["outputs"]) == 24


def test_selection_manifest_has_no_verified_claims(selection: dict) -> None:
    serialized = json.dumps(selection, ensure_ascii=False).lower()
    assert '"status": "verified"' not in serialized
    assert '"whole_corpus_checked": true' not in serialized


def test_semantic_overrides_reject_complete_answer_and_label_partial_support(
    dataset: dict, selection: dict
) -> None:
    overrides = load_json(OVERRIDES_PATH)
    selected = selection["selection"]["questions"]
    selected_upstream_ids = {item["upstream_id"] for item in selected}
    assert set(overrides["hard_rejects"]).isdisjoint(selected_upstream_ids)
    assert "64fa9b31b82641eb8ecc06ef" not in selected_upstream_ids
    new_by_upstream = {
        item["source"]["upstream_id"]: item
        for item in dataset["questions"]
        if item["answerable"] is False
    }
    for upstream_id in set(overrides["partial_support"]) & set(new_by_upstream):
        assert new_by_upstream[upstream_id]["unanswerable_type"] == "insufficient_detail"
    assert selection["semantic_review_overrides"]["human_review_complete"] is False
    assert selection["semantic_review_overrides"]["sha256"] == hashlib.sha256(
        OVERRIDES_PATH.read_bytes()
    ).hexdigest()
