import hashlib
import json

import pytest

from eval.dataset_v3 import (
    DatasetPreparationError,
    build_review_packet,
    build_corpus_manifest,
    load_json_objects,
    parse_corpus,
    prepare_dataset,
    resolve_evidence,
    validate_dataset,
)


def _review():
    return {
        "status": "draft",
        "reviewer": None,
        "reviewed_at": None,
        "rubric_version": "rag-gold-v1",
        "evidence_checked": False,
        "answer_checked": False,
        "whole_corpus_checked": False,
    }


def _candidate(snippet: str):
    return {
        "schema_version": 3,
        "questions": [
            {
                "id": "q1",
                "question_group": "g1",
                "split": None,
                "question": "RAG 有什么作用？",
                "category": "fact",
                "difficulty": "easy",
                "document_types": ["txt"],
                "answerable": True,
                "unanswerable_type": None,
                "evidence": [{"document_name": "facts.txt", "snippet": snippet}],
                "keypoints": [["检索"]],
                "reference_answer": "用于检索。",
                "review": _review(),
            }
        ],
        "dialogues": [],
    }


def test_load_json_objects_accepts_model_batches_separated_by_commas(tmp_path):
    source = tmp_path / "batches.json"
    source.write_text('{"schema_version":3},\n{"schema_version":3}\n', encoding="utf-8")

    assert len(load_json_objects(source)) == 2


def test_prepare_canonicalizes_whitespace_and_writes_stable_evidence(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "facts.txt").write_text("RAG  用于检索。", encoding="utf-8")
    parsed = parse_corpus(corpus)

    dataset, report = prepare_dataset(
        [_candidate("RAG用于检索。")],
        parsed,
        dataset_id="dataset-v1",
        corpus_id="corpus-v1",
    )

    evidence = dataset["questions"][0]["evidence"][0]
    assert evidence["snippet"] == "RAG 用于检索。"
    assert evidence["unit_id"] == "facts.txt:u0"
    assert evidence["start_char"] == 0
    assert evidence["end_char"] == len(evidence["snippet"])
    assert evidence["snippet_sha256"] == hashlib.sha256(
        evidence["snippet"].encode("utf-8")
    ).hexdigest()
    assert report["canonicalized_snippets"] == 1
    assert dataset["questions"][0]["split"] in {
        "development",
        "validation",
        "frozen_test",
    }


def test_prepare_requires_locator_for_repeated_snippet(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "facts.txt").write_text("重复。重复。", encoding="utf-8")
    parsed = parse_corpus(corpus)

    with pytest.raises(DatasetPreparationError, match="需要唯一命中"):
        resolve_evidence(
            {"document_name": "facts.txt", "snippet": "重复。"},
            parsed,
        )

    resolved, _ = resolve_evidence(
        {
            "document_name": "facts.txt",
            "snippet": "重复。",
            "unit_index": 0,
            "start_char": len("重复。"),
        },
        parsed,
    )
    assert resolved["unit_id"] == "facts.txt:u0"


def test_validate_allows_drafts_non_strict_but_rejects_them_strict(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "facts.txt").write_text("RAG 用于检索。", encoding="utf-8")
    parsed = parse_corpus(corpus)
    dataset, _ = prepare_dataset(
        [_candidate("RAG 用于检索。")],
        parsed,
        dataset_id="dataset-v1",
        corpus_id="corpus-v1",
    )
    manifest = build_corpus_manifest(corpus, parsed, corpus_id="corpus-v1")

    non_strict = validate_dataset(
        dataset,
        parsed_documents=parsed,
        manifest=manifest,
        current_manifest=manifest,
        strict=False,
    )
    strict = validate_dataset(
        dataset,
        parsed_documents=parsed,
        manifest=manifest,
        current_manifest=manifest,
        strict=True,
    )

    assert non_strict.ok
    assert non_strict.warnings
    assert not strict.ok
    assert any("review.status" in error for error in strict.errors)


def test_validate_rejects_keypoint_not_covered_by_reference_answer(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "facts.txt").write_text("RAG 用于检索。", encoding="utf-8")
    parsed = parse_corpus(corpus)
    candidate = _candidate("RAG 用于检索。")
    candidate["questions"][0]["keypoints"] = [["生成答案"]]
    dataset, _ = prepare_dataset(
        [candidate],
        parsed,
        dataset_id="dataset-v1",
        corpus_id="corpus-v1",
    )
    manifest = build_corpus_manifest(corpus, parsed, corpus_id="corpus-v1")

    result = validate_dataset(
        dataset,
        parsed_documents=parsed,
        manifest=manifest,
        current_manifest=manifest,
        strict=False,
    )

    assert not result.ok
    assert any("没有变体能命中 reference_answer" in error for error in result.errors)


def test_review_packet_contains_dataset_binding_and_evidence(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "facts.txt").write_text("RAG 用于检索。", encoding="utf-8")
    parsed = parse_corpus(corpus)
    dataset, _ = prepare_dataset(
        [_candidate("RAG 用于检索。")],
        parsed,
        dataset_id="dataset-v1",
        corpus_id="corpus-v1",
    )

    packet = build_review_packet(dataset)

    assert "# Dataset v3 人工审核包" in packet
    assert "Dataset SHA-256" in packet
    assert "### q1" in packet
    assert "RAG 用于检索。" in packet
    assert "- [ ] 每条 evidence 均直接支持对应答案" in packet
    assert "**决定：** `approve / revise / reject`" in packet


def test_manifest_detects_corpus_change(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    source = corpus / "facts.txt"
    source.write_text("第一版", encoding="utf-8")
    first_parsed = parse_corpus(corpus)
    first = build_corpus_manifest(corpus, first_parsed, corpus_id="corpus-v1")

    source.write_text("第二版", encoding="utf-8")
    second_parsed = parse_corpus(corpus)
    second = build_corpus_manifest(corpus, second_parsed, corpus_id="corpus-v1")

    assert first["parsed_corpus_sha256"] != second["parsed_corpus_sha256"]
    assert json.dumps(first, sort_keys=True) != json.dumps(second, sort_keys=True)
