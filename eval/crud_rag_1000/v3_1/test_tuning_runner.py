from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.crud_rag_1000.v3_1 import tuning_runner as runner


def _question(index: int, split: str, *, answerable: bool = True, gold: int = 1):
    return {
        "id": f"q-{split}-{index}",
        "split": split,
        "question": f"问题{index}",
        "category": "questanswer_1doc" if answerable else "unanswerable",
        "answerable": answerable,
        "source": {"gold_document_count": gold},
    }


def _dataset():
    questions = []
    for split, count in (("development", 72), ("validation", 24), ("frozen_test", 24)):
        questions.extend(_question(index, split) for index in range(count))
    questions[1]["source"]["gold_document_count"] = 2
    questions[2]["answerable"] = False
    questions[2]["category"] = "unanswerable"
    return {
        "dataset_id": "fixture-v3.1",
        "parser_version": "structured-v2",
        "rubric_version": "rag-gold-v1",
        "questions": questions,
    }


def test_config_identity_and_cache_key_bind_every_effective_dependency():
    config = json.loads(json.dumps(runner.BASELINE_CONFIG))
    config_id = runner._config_id(config)
    assert config_id.startswith("hybrid-")
    assert runner._fusion_pool_k(config) == 15
    assert runner._effective_config(config)["retrieval_mode"] == "hybrid"

    protocol = {
        "dataset": {"sha256": "dataset"},
        "source": {"storage_identity": "index"},
        "code_identity": {"code.py": "hash"},
        "environment": {"EMBEDDING_MODEL": "model"},
    }
    question = _question(1, "development")
    original = runner._cache_key(protocol, "development", question, config)
    changed = json.loads(json.dumps(config))
    changed["dense_candidate_k"] = 30
    assert runner._cache_key(protocol, "development", question, changed) != original
    changed_question = dict(question, question="另一个问题")
    assert runner._cache_key(protocol, "development", changed_question, config) != original


@pytest.mark.parametrize("value", ["../escape", "a/b", "", "x" * 101])
def test_names_reject_path_escape(value):
    with pytest.raises(ValueError):
        runner._safe_name(value)


def test_split_contract_and_smoke_cover_one_multi_and_unanswerable():
    dataset = _dataset()
    development = runner._split_questions(dataset, "development")
    assert len(development) == 72
    smoke = runner._smoke_ids(development)
    assert len(set(smoke.values())) == 3
    questions = {row["id"]: row for row in development}
    assert questions[smoke["answerable_one_document"]]["answerable"] is True
    assert questions[smoke["answerable_multi_document"]]["source"]["gold_document_count"] > 1
    assert questions[smoke["unanswerable"]]["answerable"] is False


def test_prepare_copies_storage_once_and_records_draft_protocol(tmp_path, monkeypatch):
    source_run = tmp_path / "source-run"
    source_audit = tmp_path / "source-audit"
    tuning_root = tmp_path / "tuning"
    dataset_path = tmp_path / "questions.json"
    (source_run / "chroma").mkdir(parents=True)
    (source_run / "chroma" / "chroma.sqlite3").write_bytes(b"chroma")
    (source_run / "metadata.db").write_bytes(b"metadata")
    source_audit.mkdir()
    (source_audit / "artifact-manifest.json").write_text(
        json.dumps({"files": {}}), encoding="utf-8"
    )
    dataset_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(runner, "SOURCE_RUN", source_run)
    monkeypatch.setattr(runner, "SOURCE_AUDIT", source_audit)
    monkeypatch.setattr(runner, "TUNING_ROOT", tuning_root)
    monkeypatch.setattr(runner, "DATASET_PATH", dataset_path)
    monkeypatch.setattr(runner, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(runner, "_source_artifact_checks", lambda manifest: None)
    monkeypatch.setattr(runner, "_database_checks", lambda chroma, metadata: {"fixture": "ok"})
    monkeypatch.setattr(runner, "_dataset", _dataset)
    monkeypatch.setattr(runner, "_code_identity", lambda: {"fixture.py": "hash"})

    experiment = runner.prepare_experiment("phase-a-fixture")
    protocol = json.loads((experiment / "protocol.json").read_text(encoding="utf-8"))
    result = json.loads((experiment / "prepare-result.json").read_text(encoding="utf-8"))

    assert (experiment / "storage/chroma/chroma.sqlite3").read_bytes() == b"chroma"
    assert (experiment / "storage/metadata.db").read_bytes() == b"metadata"
    assert protocol["dataset"]["review_status"] == "draft"
    assert protocol["storage"]["profile"] == "copied_test_index"
    assert protocol["cache_contract"]["cross_config_stage_cache"] is False
    assert result["source_storage_unchanged"] is True
    with pytest.raises(FileExistsError):
        runner.prepare_experiment("phase-a-fixture")


def test_cached_rows_reject_config_or_question_drift(tmp_path):
    config = json.loads(json.dumps(runner.BASELINE_CONFIG))
    question = _question(1, "development")
    protocol = {
        "dataset": {"sha256": "dataset"},
        "source": {"storage_identity": "index"},
        "code_identity": {"code.py": "hash"},
        "environment": {"MODEL": "fixed"},
    }
    rows = tmp_path / "rows.jsonl"
    cache_key = runner._cache_key(protocol, "development", question, config)
    rows.write_text(
        json.dumps({"id": question["id"], "cache_key": cache_key}) + "\n",
        encoding="utf-8",
    )
    loaded = runner._read_rows(rows, [question], protocol, "development", config)
    assert set(loaded) == {question["id"]}

    changed = json.loads(json.dumps(config))
    changed["rrf_k"] = 30
    with pytest.raises(RuntimeError, match="缓存键失效"):
        runner._read_rows(rows, [question], protocol, "development", changed)


def test_runtime_storage_allows_only_chroma_sqlite_bookkeeping(tmp_path, monkeypatch):
    experiment = tmp_path / "experiment"
    chroma = experiment / "storage/chroma"
    chroma.mkdir(parents=True)
    (chroma / "chroma.sqlite3").write_bytes(b"before")
    (chroma / "index.bin").write_bytes(b"index")
    (experiment / "storage/metadata.db").write_bytes(b"metadata")
    manifest = runner._storage_manifest(
        chroma,
        experiment / "storage/metadata.db",
    )
    protocol = {
        "code_identity": {"retrieval.py": "hash"},
        "storage": {
            "manifest": manifest,
            "database_checks": {"logical": "same"},
        },
    }
    monkeypatch.setattr(
        runner,
        "_retrieval_code_identity",
        lambda: {"retrieval.py": "hash"},
    )
    monkeypatch.setattr(
        runner,
        "_database_checks",
        lambda chroma_path, metadata_path: {"logical": "same"},
    )

    (chroma / "chroma.sqlite3").write_bytes(b"runtime-bookkeeping")
    result = runner._validate_experiment_storage(experiment, protocol)
    assert result["chroma_sqlite_byte_hash_changed_after_open"] is True
    assert result["immutable_files_match_prepared_copy"] is True

    (chroma / "index.bin").write_bytes(b"changed-index")
    with pytest.raises(RuntimeError, match="不可变文件已变化"):
        runner._validate_experiment_storage(experiment, protocol)


def test_a_phase_parser_exposes_prepare_and_run_without_overwrite_switch():
    parser = runner.build_parser()
    prepare = parser.parse_args(["prepare", "--experiment-id", "phase-a"])
    register = parser.parse_args([
        "register-config",
        "--experiment-id",
        "phase-a",
        "--config-json",
        "candidate.json",
    ])
    accept = parser.parse_args(["accept-a", "--experiment-id", "phase-a"])
    run = parser.parse_args(["run", "--experiment-id", "phase-a", "--resume"])
    assert prepare.command == "prepare"
    assert register.command == "register-config"
    assert accept.command == "accept-a"
    assert run.command == "run" and run.resume is True
    assert all(
        "overwrite" not in vars(item)
        for item in (prepare, register, accept, run)
    )


def test_validation_rejects_unknown_config_fields():
    payload = json.loads(json.dumps(runner.BASELINE_CONFIG))
    assert runner._validated_config_payload(payload) == payload
    payload["unknown"] = 1
    with pytest.raises(ValueError):
        runner._validated_config_payload(payload)
