"""Hugging Face 本地模型快照解析测试。"""

import pytest

from backend.services.model_cache import resolve_cached_snapshot


def test_resolve_cached_snapshot_never_requests_network(monkeypatch):
    calls = []

    def fake_snapshot_download(**kwargs):
        calls.append(kwargs)
        return "C:/cache/model/snapshot"

    monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot_download)

    path = resolve_cached_snapshot("BAAI/test-model")

    assert path == "C:/cache/model/snapshot"
    assert calls == [
        {"repo_id": "BAAI/test-model", "local_files_only": True}
    ]


def test_resolve_cached_snapshot_has_actionable_missing_model_error(monkeypatch):
    from huggingface_hub.errors import LocalEntryNotFoundError

    def raise_missing(**kwargs):
        raise LocalEntryNotFoundError("missing")

    monkeypatch.setattr("huggingface_hub.snapshot_download", raise_missing)

    with pytest.raises(RuntimeError, match="BAAI/missing-model"):
        resolve_cached_snapshot("BAAI/missing-model")
