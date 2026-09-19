"""Offline acceptance for the new QA-only revision, without model inference."""

from __future__ import annotations

from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import pytest

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from eval.crud_rag_1000.v3_1 import build_revision as revision  # noqa: E402
from eval.crud_rag_1000.v3_1 import run_retrieval_baseline as runner  # noqa: E402
from eval.dataset_runtime import normalize_for_evaluation  # noqa: E402
from eval.dataset_v3 import (  # noqa: E402
    build_corpus_manifest,
    parse_corpus,
    resolve_evidence,
    validate_dataset,
)


def load(name: str) -> dict:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def parent():
    return revision.read_json(revision.PARENT_DATASET)


@pytest.fixture(scope="module")
def dataset():
    return load("questions_v3_120q.json")


@pytest.fixture(scope="module")
def changes():
    return load("change_manifest.json")["corrections"]


@pytest.fixture(scope="module")
def parsed():
    return parse_corpus(revision.CORPUS_ROOT)


def test_new_version_counts_and_identity(parent, dataset):
    assert dataset["schema_version"] == 3
    assert dataset["dataset_id"] == "crud-rag-1000-120q-v3.1"
    assert dataset["dataset_id"] != parent["dataset_id"]
    assert dataset["corpus_id"] == parent["corpus_id"]
    assert dataset["parser_version"] == parent["parser_version"] == "structured-v2"
    assert dataset["rubric_version"] == parent["rubric_version"]
    assert len(dataset["questions"]) == 120
    assert sum(q["answerable"] for q in dataset["questions"]) == 100
    assert len({q["id"] for q in dataset["questions"]}) == 120
    assert len({q["question_group"] for q in dataset["questions"]}) == 120
    assert len({q["question"] for q in dataset["questions"]}) == 120
    assert Counter(q["category"] for q in dataset["questions"] if q["answerable"]) == {
        "questanswer_1doc": 37, "questanswer_2docs": 33, "questanswer_3docs": 30,
    }
    assert Counter(q["split"] for q in dataset["questions"]) == {
        "development": 72, "validation": 24, "frozen_test": 24,
    }


def test_exactly_ten_changed_and_all_others_identical(parent, dataset, changes):
    expected = {change["old_id"] for change in changes}
    actual = {
        old["id"] for old, new in zip(parent["questions"], dataset["questions"], strict=True)
        if old != new
    }
    assert len(actual) == 10
    assert actual == expected
    assert dataset["dialogues"] == parent["dialogues"] == []
    for field in parent.keys() - {"dataset_id", "questions"}:
        assert parent[field] == dataset[field]


def test_unanswerables_unchanged(parent, dataset):
    before = [q for q in parent["questions"] if not q["answerable"]]
    after = [q for q in dataset["questions"] if not q["answerable"]]
    assert before == after
    assert len(after) == 20
    assert all(q["evidence"] == [] and q["keypoints"] == [] for q in after)


def test_review_split_and_raw_upstream_are_preserved(parent, dataset):
    for old, new in zip(parent["questions"], dataset["questions"], strict=True):
        assert old["split"] == new["split"]
        assert old["review"] == new["review"]
        assert new["review"]["status"] == "draft"
        assert new["review"]["reviewer"] is None
        assert new["review"]["reviewed_at"] is None
        for flag in ("evidence_checked", "answer_checked", "whole_corpus_checked"):
            assert new["review"][flag] is False
        for key, value in old["source"].items():
            if key != "gold_document_count":
                assert new["source"][key] == value


def test_mapping_is_complete_bijective_and_matches_actual_changes(parent, dataset):
    mapping = load("id_mapping.json")["mapping"]
    assert len(mapping) == 120
    assert len({m["old_id"] for m in mapping}) == len({m["new_id"] for m in mapping}) == 120
    assert sum(m["content_changed"] for m in mapping) == 10
    assert sum(m["id_changed"] for m in mapping) == 5
    for m, old, new in zip(mapping, parent["questions"], dataset["questions"], strict=True):
        assert (m["old_id"], m["new_id"]) == (old["id"], new["id"])
        assert m["query_changed"] == (old["question"] != new["question"])
        count_changed = old["source"].get("gold_document_count") != new["source"].get("gold_document_count")
        assert m["id_changed"] == count_changed
        assert (m["old_question_group"] != m["new_question_group"]) == count_changed


def test_effective_gold_counts_and_categories(dataset):
    names = {1: "questanswer_1doc", 2: "questanswer_2docs", 3: "questanswer_3docs"}
    for q in dataset["questions"]:
        if q["answerable"]:
            count = len({ev["document_name"] for ev in q["evidence"]})
            assert q["source"]["gold_document_count"] == count
            assert q["category"] == names[count]
            assert q["keypoints"] == [[q["reference_answer"]]]
    selection = load("selection_manifest.json")
    gold = {e["document_name"] for q in dataset["questions"] for e in q["evidence"]}
    assert set(selection["active_gold_documents"]) == gold
    assert gold.isdisjoint(selection["non_gold_corpus_documents"])
    assert len(gold) + len(selection["non_gold_corpus_documents"]) == 1000


def test_all_evidence_exact_unique_and_hashed(dataset, parsed):
    for question in dataset["questions"]:
        for evidence in question["evidence"]:
            resolved, canonicalized = resolve_evidence(evidence, parsed)
            assert resolved == evidence
            assert not canonicalized
            assert hashlib.sha256(evidence["snippet"].encode("utf-8")).hexdigest() == evidence["snippet_sha256"]
            document = parsed[evidence["document_name"]]
            assert sum(u.canonical_text.count(evidence["snippet"]) for u in document.units) == 1


def test_manifest_and_project_validation(dataset, parsed):
    manifest = revision.read_json(revision.CORPUS_MANIFEST)
    current = build_corpus_manifest(revision.CORPUS_ROOT, parsed, corpus_id=dataset["corpus_id"])
    assert manifest == current
    assert len(current["documents"]) == 1000
    normal = validate_dataset(dataset, parsed_documents=parsed, manifest=manifest, current_manifest=current, strict=False)
    strict = validate_dataset(dataset, parsed_documents=parsed, manifest=manifest, current_manifest=current, strict=True)
    assert normal.ok, normal.errors
    assert len(normal.warnings) == 120
    assert normal.stats["evidence"] == 193
    assert not strict.ok
    assert len(strict.errors) == 620
    assert all(any(token in error for token in (
        "review.status", "reviewer", "reviewed_at", "evidence_checked", "answer_checked", "whole_corpus_checked",
    )) for error in strict.errors)
    assert normalize_for_evaluation(dataset)["version"] == dataset["dataset_id"]


@pytest.mark.parametrize("risk_id", [f"H-A{i}" for i in range(1, 11)])
def test_content_corrections_regressions(risk_id, changes):
    record = next(r for r in changes if r["risk_id"] == risk_id)
    q = record["after"]
    answer, question = q["reference_answer"], q["question"]
    evidence = "\n".join(e["snippet"] for e in q["evidence"])
    if risk_id in {"H-A1", "H-A2", "H-A3"}:
        assert "2021" not in answer + question
        assert "2023" not in answer + question
    if risk_id == "H-A4":
        assert "0.10" not in answer and "0.17" not in answer
        assert all(value in evidence for value in ("3800", "4300", "4800", "5300", "1950"))
        assert "这些过渡地带" in answer
    if risk_id == "H-A5":
        assert q["evidence"][0]["document_name"] == "support/f1a805d607b8d845.txt"
        assert "宣布将加强" in answer and "称将停止" in answer
    if risk_id == "H-A6":
        assert "卓资县" not in evidence
        assert "五大曝光" not in answer and "减量控大" not in answer
        assert all(value in evidence for value in ("市区", "新六区", "高速", "三下降"))
    if risk_id == "H-A7":
        assert all(value in answer for value in ("美国", "盟友", "日本", "尚未"))
        assert "应特别关注日本" in evidence
    if risk_id == "H-A8":
        assert "撤稿" not in answer and "目前团队已要求下" not in evidence
        assert "影像和论文" in question and "擅自发布" in answer
    if risk_id == "H-A9":
        assert "阿尔及利亚朋友们" in evidence
        assert "阿尔及利亚" in answer and "阿联酋" not in answer
        assert "阿联酋" in q["source"]["upstream_event"]
        assert all(value in evidence for value in ("驻巴基斯坦", "驻智利", "联合国维和", "亚丁湾"))
        assert "军事交流" not in answer
    if risk_id == "H-A10":
        assert "第10年" in question and "第10年" in evidence
        assert q["evidence"][0]["document_name"] == "support/8449ff9ebaab7130.txt"
        assert "7月18日" in answer and "计划持续到8月中旬" in answer
        assert "7月13日" not in answer and "第11" not in evidence


def test_changelog_matches_parent_new_and_actual_field_diff(parent, dataset, changes):
    old_by_id = {q["id"]: q for q in parent["questions"]}
    new_by_id = {q["id"]: q for q in dataset["questions"]}
    for record in changes:
        assert record["before"] == old_by_id[record["old_id"]]
        assert record["after"] == new_by_id[record["new_id"]]
        assert record["changed_fields"] == revision.changed_fields(record["before"], record["after"])


def test_original_static_artifacts_are_unchanged():
    saved = load("source_preservation.json")
    assert saved["unchanged"] is True
    assert saved["files"] == revision.protected_snapshot()
    assert revision.sha256(revision.PARENT_DATASET) == "9bdf414ab4a239995be095c9af852a6aa86780690adc8719c92a53a8df79a339"
    assert revision.sha256(revision.CORPUS_MANIFEST) == "f81dc69844cd38647e35af27baa0ced76628629242d2682ea48694390ddd6636"


def test_outputs_match_provenance():
    provenance = load("provenance.json")
    for artifact in provenance["outputs"]:
        path = ROOT / artifact["path"]
        assert revision.sha256(path) == artifact["sha256"]
        assert path.stat().st_size == artifact["bytes"]
    assert revision.sha256(ROOT / "correction_spec.json") == provenance["correction_spec"]["sha256"]
    assert revision.sha256(ROOT / "build_revision.py") == provenance["generator"]["sha256"]
    assert provenance["project_llm_api_called"] is False
    assert provenance["database_used"] is False
    assert provenance["retrieval_rerun"] is False


@pytest.mark.parametrize("reverse_snapshot_order", [False, True])
def test_deterministic_generation_without_writes(monkeypatch, reverse_snapshot_order):
    # 冻结生成器以Path排序：Windows忽略大小写，Linux不忽略。
    # 验证完整路径/hash/长度后按冻结清单顺序输入；不修改生成器或原始产物。
    saved_files = load("source_preservation.json")["files"]
    original_snapshot = revision.protected_snapshot

    def snapshot_in_frozen_order():
        current = original_snapshot()
        if reverse_snapshot_order:
            current = dict(reversed(list(current.items())))
        assert current == saved_files
        return {path: current[path] for path in saved_files}

    monkeypatch.setattr(revision, "protected_snapshot", snapshot_in_frozen_order)
    artifacts, summary = revision.build_artifacts()
    assert summary["changed_questions"] == 10
    revision.publish(artifacts, ROOT, check=True)


def test_invalid_scope_or_unknown_question_rejected(parent, parsed):
    spec = load("correction_spec.json")
    invalid = copy.deepcopy(spec)
    invalid["corrections"].pop()
    with pytest.raises(ValueError, match="exactly 10"):
        revision.revise_dataset(parent, invalid, parsed)
    invalid = copy.deepcopy(spec)
    invalid["corrections"][0]["old_id"] = "unknown"
    with pytest.raises(ValueError, match="exactly 10"):
        revision.revise_dataset(parent, invalid, parsed)


def test_publisher_rejects_parent_directory_before_any_write():
    with pytest.raises(ValueError, match="new v3_1"):
        revision.publish({"must-not-exist.json": b"{}"}, revision.PARENT_ROOT, check=False)
    assert not (revision.PARENT_ROOT / "must-not-exist.json").exists()


def test_publisher_preflights_all_outputs_and_refuses_overwrite():
    # 发布器只允许写入v3_1内部；不依赖调用方额外指定pytest --basetemp。
    with TemporaryDirectory(prefix=".pytest-publisher-", dir=ROOT) as directory:
        output = Path(directory).resolve()
        assert output.is_relative_to(ROOT)
        existing = output / "already.json"
        existing.write_bytes(b"original")
        with pytest.raises(FileExistsError, match="Refusing to overwrite"):
            revision.publish({"new.json": b"new", "already.json": b"changed"}, output, check=False)
        assert existing.read_bytes() == b"original"
        assert not (output / "new.json").exists()


def test_new_runner_pins_new_dataset_and_does_not_allow_overwrite(capsys):
    argv = runner.build_eval_argv(label="future-v31", profile="gpu")
    assert argv[0] == "retrieval-sweep"
    assert argv[argv.index("--dataset") + 1] == str(ROOT / "questions_v3_120q.json")
    assert argv[argv.index("--corpus") + 1] == str(revision.CORPUS_ROOT)
    assert argv[argv.index("--manifest") + 1] == str(revision.CORPUS_MANIFEST)
    assert argv[argv.index("--embedding-device") + 1] == "cuda:0"
    assert argv[argv.index("--reranker-device") + 1] == "cuda:0"
    assert "--overwrite" not in argv
    assert "--allow-draft-v3" in argv
    with pytest.raises(ValueError, match="label"):
        runner.build_eval_argv(label="../v3", profile="gpu")
    assert runner.main(["--profile", "gpu", "--label", "preview-only", "--dry-run"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["dry_run"] is True
    assert preview["run_directory"] == str(ROOT / "runs" / "preview-only")
    assert not (ROOT / "runs" / "preview-only").exists()
