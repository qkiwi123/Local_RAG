"""Offline acceptance checks for the local CRUD_RAG Dataset v3 fixture."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from eval.dataset_v3 import (  # noqa: E402
    build_corpus_manifest,
    parse_corpus,
    validate_dataset,
)
from eval import adapters as eval_adapters  # noqa: E402
from eval.crud_rag_1000.prepare_dataset import normalize_text  # noqa: E402
from eval.crud_rag_1000.run_retrieval_sweep import build_eval_argv  # noqa: E402


DATASET_PATH = ROOT / "questions_v3.json"
MANIFEST_PATH = ROOT / "corpus_v3_manifest.json"
SELECTION_PATH = ROOT / "selection_manifest.json"
PROVENANCE_PATH = ROOT / "provenance.json"
CORPUS_DIR = ROOT / "corpus"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


@pytest.fixture(scope="module")
def dataset() -> dict:
    return load_json(DATASET_PATH)


@pytest.fixture(scope="module")
def selection() -> dict:
    return load_json(SELECTION_PATH)


@pytest.fixture(scope="module")
def parsed_documents():
    return parse_corpus(CORPUS_DIR)


@pytest.fixture(scope="module")
def upstream() -> dict:
    return load_json(ROOT / "source" / "split_merged.json")


def test_exact_question_and_corpus_counts(dataset: dict, selection: dict) -> None:
    questions = dataset["questions"]
    assert len(questions) == 100
    assert len(selection["selection"]["questions"]) == 100
    corpus_files = sorted(
        path.relative_to(CORPUS_DIR).as_posix()
        for path in CORPUS_DIR.rglob("*.txt")
        if path.is_file()
    )
    assert len(corpus_files) == 1000
    assert len([name for name in corpus_files if name.startswith("support/")]) == 193
    assert len([name for name in corpus_files if name.startswith("distractor/")]) == 807
    assert selection["corpus"]["gold_document_count"] == 193
    assert selection["corpus"]["distractor_document_count"] == 807
    assert selection["corpus"]["total_document_count"] == 1000


def test_stratified_distribution_and_unique_identity(dataset: dict, selection: dict) -> None:
    expected = {
        "questanswer_1doc": 34,
        "questanswer_2docs": 33,
        "questanswer_3docs": 33,
    }
    actual = {
        category: sum(item["category"] == category for item in dataset["questions"])
        for category in expected
    }
    assert actual == expected
    assert selection["selection"]["actual_distribution"] == expected
    assert len({item["id"] for item in dataset["questions"]}) == 100
    assert len({item["question"] for item in dataset["questions"]}) == 100
    assert selection["selection"]["reallocation"] == []


def test_evidence_is_exact_and_unique(dataset: dict, parsed_documents) -> None:
    for question in dataset["questions"]:
        assert question["answerable"] is True
        assert question["review"]["status"] == "draft"
        assert question["review"]["reviewer"] is None
        assert question["review"]["reviewed_at"] is None
        assert question["review"]["evidence_checked"] is False
        assert question["review"]["answer_checked"] is False
        assert question["review"]["whole_corpus_checked"] is False
        for evidence in question["evidence"]:
            name = evidence["document_name"]
            assert name in parsed_documents
            document = parsed_documents[name]
            locator = evidence["locator"]
            matching_units = [
                unit
                for unit in document.units
                if unit.locator.unit_index == locator["unit_index"]
            ]
            assert len(matching_units) == 1
            unit = matching_units[0]
            snippet = evidence["snippet"]
            assert unit.canonical_text[evidence["start_char"] : evidence["end_char"]] == snippet
            assert sum(unit_item.canonical_text.count(snippet) for unit_item in document.units) == 1
            assert hashlib.sha256(snippet.encode("utf-8")).hexdigest() == evidence["snippet_sha256"]


def test_gold_and_distractor_sets_are_disjoint(selection: dict) -> None:
    gold = set(selection["corpus"]["gold_documents"])
    distractors = set(selection["corpus"]["distractor_documents"])
    assert len(gold) == 193
    assert len(distractors) == 807
    assert gold.isdisjoint(distractors)
    assert gold == {name for name in gold if name.startswith("support/")}
    assert distractors == {name for name in distractors if name.startswith("distractor/")}

    support_hashes = {
        hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (CORPUS_DIR / "support").glob("*.txt")
    }
    distractor_hashes = {
        hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (CORPUS_DIR / "distractor").glob("*.txt")
    }
    assert len(support_hashes) == 193
    assert len(distractor_hashes) == 807
    assert support_hashes.isdisjoint(distractor_hashes)

    distractor_records = selection["distractor"]["documents"]
    assert len(distractor_records) == 807
    assert {item["document_name"] for item in distractor_records} == distractors
    for item in distractor_records:
        text = (CORPUS_DIR / item["document_name"]).read_text(encoding="utf-8")
        assert hashlib.sha256(text.rstrip().encode("utf-8")).hexdigest() == item["sha256"]


def test_question_gold_mapping_is_closed(dataset: dict, selection: dict) -> None:
    used_documents = {
        evidence["document_name"]
        for question in dataset["questions"]
        for evidence in question["evidence"]
    }
    assert used_documents == set(selection["corpus"]["gold_documents"])

    mapping = selection["corpus"]["question_gold_mapping"]
    assert len(mapping) == len(dataset["questions"])
    for question, source in zip(dataset["questions"], mapping, strict=True):
        assert question["source"]["upstream_task"] == source["category"]
        assert question["source"]["upstream_id"] == source["upstream_id"]
        assert [item["document_name"] for item in question["evidence"]] == source[
            "gold_documents"
        ]


def test_selected_questions_and_gold_text_match_upstream(
    dataset: dict, upstream: dict
) -> None:
    rows = {
        (category, item["ID"]): item
        for category in ("questanswer_1doc", "questanswer_2docs", "questanswer_3docs")
        for item in upstream[category]
    }
    for question in dataset["questions"]:
        category = question["source"]["upstream_task"]
        source = rows[(category, question["source"]["upstream_id"])]
        assert question["question"] == source["questions"].strip()
        assert question["reference_answer"] == source["answers"].strip()
        expected_gold = [
            normalize_text(source[f"news{index}"])
            for index in range(1, question["source"]["gold_document_count"] + 1)
        ]
        actual_gold = [
            (CORPUS_DIR / evidence["document_name"])
            .read_text(encoding="utf-8")
            .rstrip()
            for evidence in question["evidence"]
        ]
        assert actual_gold == expected_gold


def test_project_eval_adapter_discovers_nested_corpus(selection: dict) -> None:
    parsed = eval_adapters.parse_corpus(CORPUS_DIR, structured=True)
    expected = set(selection["corpus"]["gold_documents"]) | set(
        selection["corpus"]["distractor_documents"]
    )
    assert len(parsed) == 1000
    assert set(parsed) == expected


def test_manifest_matches_current_corpus(dataset: dict, parsed_documents) -> None:
    manifest = load_json(MANIFEST_PATH)
    current = build_corpus_manifest(
        CORPUS_DIR,
        parsed_documents,
        corpus_id=dataset["corpus_id"],
    )
    assert manifest == current
    result = validate_dataset(
        dataset,
        parsed_documents=parsed_documents,
        manifest=manifest,
        current_manifest=current,
        strict=False,
    )
    assert result.ok, result.errors
    assert result.stats["questions"] == 100
    assert result.stats["evidence"] == 199


def test_source_hashes_and_pinned_urls(selection: dict) -> None:
    provenance = load_json(PROVENANCE_PATH)
    expected_commit = selection["upstream"]["commit"]
    assert expected_commit == "1aace383994e1f68efa12cf2a8e2dadfb4102ceb"
    entries = selection["upstream"]["source_files"]
    assert entries == provenance["upstream"]["source_files"]
    assert len(entries) == 4
    for entry in entries:
        source_path = ROOT / entry["path"]
        assert source_path.is_file()
        assert source_path.stat().st_size == entry["bytes"]
        digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
        assert digest == entry["sha256"]
        assert expected_commit in entry["raw_url"]


def test_dataset_identity_and_review_state(dataset: dict) -> None:
    assert dataset["schema_version"] == 3
    assert dataset["parser_version"] == "structured-v2"
    assert dataset["rubric_version"] == "rag-gold-v1"
    assert dataset["dialogues"] == []
    assert all(item["document_types"] == ["txt"] for item in dataset["questions"])
    assert all(item["keypoints"] == [[item["reference_answer"]]] for item in dataset["questions"])


def test_retrieval_runner_pins_fixture_paths() -> None:
    argv = build_eval_argv(["--label", "smoke", "--candidate-ks", "15"])
    assert argv[0] == "retrieval-sweep"
    assert "--allow-draft-v3" in argv
    assert str(DATASET_PATH) in argv
    assert str(CORPUS_DIR) in argv
    assert str(MANIFEST_PATH) in argv
    with pytest.raises(ValueError, match="不可覆盖"):
        build_eval_argv(["--corpus", "elsewhere"])
