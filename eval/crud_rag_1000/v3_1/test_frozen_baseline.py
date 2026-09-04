"""Pure checks for the audited GPU entry point; no model or backend import."""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from eval.crud_rag_1000.v3_1 import execute_frozen_baseline as audit


def test_paths_stay_in_new_version():
    run, evidence = audit.checked_paths("unit-preview-frozen")
    assert run.is_relative_to(audit.ROOT)
    assert evidence.is_relative_to(audit.ROOT)
    assert run.parent.name == "runs"
    assert evidence.parent.name == "baseline_audits"


@pytest.mark.parametrize("label", ["", "../production", "a/b", "a\\b", "/absolute", "a" * 101])
def test_unsafe_label_rejected(label):
    with pytest.raises(ValueError):
        audit.checked_paths(label)


def test_existing_output_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    (tmp_path / "runs/existing").mkdir(parents=True)
    with pytest.raises(FileExistsError):
        audit.checked_paths("existing")


def test_exact_baseline_pins():
    argv = audit.runner.build_eval_argv(label="unit-preview-frozen", profile="gpu")
    assert argv[0] == "retrieval-sweep"
    assert argv[argv.index("--candidate-ks") + 1] == "15"
    assert argv[argv.index("--top-k") + 1] == "5"
    assert argv[argv.index("--distance-threshold") + 1] == "0.67"
    assert "--overwrite" not in argv
    assert audit.PINNED_ENV["DENSE_CANDIDATE_K"] == "15"
    assert audit.PINNED_ENV["MAX_CHUNKS_PER_DOCUMENT"] == "3"
    assert audit.PINNED_ENV["LLM_ENABLED"] == "false"
