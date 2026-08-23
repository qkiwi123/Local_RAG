from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.config import settings
from backend.main import app
from backend.services.metadata_schema import bootstrap_structured_database
from backend.services.runtime_settings import RuntimeSettingsManager
from backend.services.secret_store import SecretStore
from backend.services import settings_status


class _FakeProtector:
    prefix = b"SETTINGS-TEST:"

    def protect(self, plaintext: bytes) -> bytes:
        return self.prefix + plaintext[::-1]

    def unprotect(self, ciphertext: bytes) -> bytes:
        if not ciphertext.startswith(self.prefix):
            raise ValueError("invalid ciphertext")
        return ciphertext[len(self.prefix):][::-1]


class _ColdGetter:
    def __init__(self):
        self.calls = 0

    def cache_info(self):
        return SimpleNamespace(currsize=0)

    def __call__(self):
        self.calls += 1
        raise AssertionError("cold getter must not be called")


class _WarmGetter:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def cache_info(self):
        return SimpleNamespace(currsize=1)

    def __call__(self):
        self.calls += 1
        return self.value


def _isolated_manager(tmp_path, *, runtime_secret=None):
    db_path = str(tmp_path / "metadata.db")
    bootstrap_structured_database(
        db_path,
        created_at="2026-08-24T00:00:00+00:00",
    )
    secret_store = SecretStore(
        tmp_path / "runtime_secrets.bin",
        protector=_FakeProtector(),
    )
    if runtime_secret is not None:
        secret_store.set_secret("llm_api_key", runtime_secret)
    return RuntimeSettingsManager(
        db_path=db_path,
        environment=settings,
        secret_store=secret_store,
    )


def test_get_settings_returns_effective_values_sources_and_masked_key():
    with TestClient(app) as client:
        response = client.get("/api/settings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["revision"] == 0
    assert payload["llm"]["model"] == settings.DEEPSEEK_MODEL
    assert payload["retrieval"]["final_top_k"] == settings.RETRIEVAL_TOP_K
    assert payload["llm"]["api_key"] == {
        "configured": True,
        "source": "environment",
        "masked_suffix": "used",
    }
    assert "test-key-not-used" not in response.text
    assert payload["field_sources"]["llm.model"] == "environment"
    assert payload["field_effects"]["llm.model"] == "pending_s3"
    assert payload["field_effects"]["retrieval.final_top_k"] == "pending_s4"
    assert (
        payload["field_effects"]["retrieval.refusal_gate.enabled"]
        == "unavailable"
    )
    assert payload["runtime"]["access_scope"] == "trusted_local_workspace"


def test_get_settings_reports_runtime_override_without_leaking_secret(
    tmp_path,
):
    secret = "runtime-api-secret-2468"
    manager = _isolated_manager(tmp_path, runtime_secret=secret)
    manager.update(
        {"llm": {"model": "runtime-model"}},
        expected_revision=0,
    )

    with TestClient(app) as client:
        original = app.state.runtime_settings_manager
        app.state.runtime_settings_manager = manager
        try:
            response = client.get("/api/settings")
        finally:
            app.state.runtime_settings_manager = original

    payload = response.json()
    assert response.status_code == 200
    assert payload["revision"] == 1
    assert payload["llm"]["model"] == "runtime-model"
    assert payload["field_sources"]["llm.model"] == "runtime"
    assert payload["llm"]["api_key"] == {
        "configured": True,
        "source": "runtime",
        "masked_suffix": "2468",
    }
    assert secret not in response.text


def test_runtime_status_does_not_call_cold_model_getters(monkeypatch):
    from backend.services import embedding, reranker

    embedding_getter = _ColdGetter()
    reranker_getter = _ColdGetter()
    monkeypatch.setattr(embedding, "_get_model", embedding_getter)
    monkeypatch.setattr(reranker, "_get_model", reranker_getter)
    monkeypatch.setattr(settings_status, "_model_cache_available", lambda _: True)

    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        response = client.get("/api/settings/runtime-status")
        assert client.get("/api/health").status_code == 200

    assert response.status_code == 200
    payload = response.json()
    assert embedding_getter.calls == 0
    assert reranker_getter.calls == 0
    assert payload["embedding"]["loaded"] is False
    assert payload["embedding"]["actual_device"] is None
    assert payload["embedding"]["verified"] is False
    assert payload["reranker"]["loaded"] is False
    assert payload["embedding"]["configured_device"] == settings.EMBEDDING_DEVICE
    assert payload["index"]["parser_version"] == "structured-v2"
    assert payload["index"]["storage_profile"] == "test"
    assert payload["index"]["chroma_directory"] == Path(
        settings.CHROMA_PERSIST_DIR
    ).name
    assert payload["index"]["metadata_database"] == Path(
        settings.METADATA_DB_PATH
    ).name
    assert settings.CHROMA_PERSIST_DIR not in response.text
    assert settings.METADATA_DB_PATH not in response.text


def test_runtime_status_verifies_already_cached_model_devices(
    monkeypatch,
    tmp_path,
):
    from backend.services import embedding, reranker

    embedding_getter = _WarmGetter(SimpleNamespace(device="cpu"))
    reranker_getter = _WarmGetter(
        SimpleNamespace(model=SimpleNamespace(device="cpu"))
    )
    monkeypatch.setattr(embedding, "_get_model", embedding_getter)
    monkeypatch.setattr(reranker, "_get_model", reranker_getter)
    monkeypatch.setattr(settings_status, "_model_cache_available", lambda _: True)
    snapshot = _isolated_manager(tmp_path).snapshot()

    payload = settings_status.build_runtime_status(snapshot).model_dump()

    assert embedding_getter.calls == 1
    assert reranker_getter.calls == 1
    assert payload["embedding"]["loaded"] is True
    assert payload["embedding"]["actual_device"] == "cpu"
    assert payload["embedding"]["verified"] is True
    assert payload["reranker"]["loaded"] is True
    assert payload["reranker"]["actual_device"] == "cpu"
    assert payload["reranker"]["verified"] is True


def test_s2_routes_are_read_only():
    mutating_methods = {"POST", "PUT", "PATCH", "DELETE"}
    mutating_settings_routes = [
        (route.path, sorted(route.methods & mutating_methods))
        for route in app.routes
        if getattr(route, "path", "").startswith("/api/settings")
        and getattr(route, "methods", set()) & mutating_methods
    ]

    assert mutating_settings_routes == []
    with TestClient(app) as client:
        assert client.patch("/api/settings", json={}).status_code == 405
