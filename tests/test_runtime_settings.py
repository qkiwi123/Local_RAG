from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from backend.config import settings
from backend.services.metadata_schema import bootstrap_structured_database
from backend.services.runtime_settings import (
    RuntimeSettingsManager,
    RuntimeSettingsValidationError,
)
from backend.services.secret_store import SecretStore
from backend.services.settings_store import SettingsRevisionConflict, SettingsStore
from tests.test_secret_store import FakeProtector


CREATED_AT = "2026-08-24T00:00:00+00:00"


def _environment(**overrides):
    values = {
        "METADATA_DB_PATH": "unused.db",
        "DEEPSEEK_API_KEY": "environment-secret-9876",
        "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
        "DEEPSEEK_MODEL": "deepseek-v4-flash",
        "RETRIEVAL_TOP_K": 5,
        "RETRIEVAL_DISTANCE_THRESHOLD": 0.67,
        "RETRIEVAL_MODE": "dense",
        "BM25_TOP_K": 15,
        "BM25_K1": 1.5,
        "BM25_B": 0.75,
        "RRF_K": 60,
        "RERANKER_ENABLED": False,
        "RERANKER_CANDIDATE_K": 15,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _manager(temp_dir, *, environment=None, secret_value=None):
    db_path = f"{temp_dir}/metadata.db"
    bootstrap_structured_database(db_path, created_at=CREATED_AT)
    secret_store = SecretStore(
        f"{temp_dir}/runtime_secrets.bin",
        protector=FakeProtector(),
    )
    if secret_value is not None:
        secret_store.set_secret("llm_api_key", secret_value)
    return RuntimeSettingsManager(
        db_path=db_path,
        environment=environment or _environment(METADATA_DB_PATH=db_path),
        secret_store=secret_store,
    )


@pytest.mark.parametrize(
    ("attribute", "value", "message"),
    [
        ("LLM_PROVIDER", "unsupported", "LLM_PROVIDER"),
        ("LLM_TEMPERATURE", 2.1, "LLM_TEMPERATURE"),
        ("LLM_MAX_TOKENS", 0, "LLM_MAX_TOKENS"),
        ("LLM_TIMEOUT_SECONDS", 0, "LLM_TIMEOUT_SECONDS"),
        ("DEEPSEEK_MODEL", "", "DEEPSEEK_MODEL"),
        ("DEEPSEEK_BASE_URL", "file:///tmp/model", "DEEPSEEK_BASE_URL"),
        (
            "DEEPSEEK_BASE_URL",
            "https://api.example.com/v1?token=secret",
            "DEEPSEEK_BASE_URL",
        ),
        (
            "DEEPSEEK_BASE_URL",
            "https://api.example.com/v1#fragment",
            "DEEPSEEK_BASE_URL",
        ),
        ("RETRIEVAL_TOP_K", 21, "RETRIEVAL_TOP_K"),
        ("DENSE_CANDIDATE_K", 4, "DENSE_CANDIDATE_K"),
        ("MAX_CHUNKS_PER_DOCUMENT", 6, "MAX_CHUNKS_PER_DOCUMENT"),
    ],
)
def test_new_deployment_defaults_have_startup_validation(
    monkeypatch,
    attribute,
    value,
    message,
):
    monkeypatch.setattr(settings, attribute, value, raising=False)

    with pytest.raises(ValueError, match=message):
        settings._validate()


def test_no_persisted_row_matches_environment_and_does_not_write_database(temp_dir):
    manager = _manager(temp_dir)

    snapshot = manager.snapshot()

    assert snapshot.revision == 0
    assert snapshot.updated_at is None
    assert snapshot.llm.model == "deepseek-v4-flash"
    assert snapshot.llm.temperature == 0.3
    assert snapshot.llm.answer_thinking_mode == "disabled"
    assert snapshot.llm.answer_reasoning_effort == "provider_default"
    assert snapshot.llm.rewrite_enabled is True
    assert snapshot.llm.rewrite_thinking_mode == "disabled"
    assert snapshot.llm.rewrite_temperature == 0
    assert snapshot.llm.rewrite_max_tokens == 200
    assert snapshot.llm.rewrite_timeout_seconds == 30
    assert snapshot.retrieval.final_top_k == 5
    assert snapshot.retrieval.dense_candidate_k == 10
    assert snapshot.retrieval.max_chunks_per_document == 3
    assert snapshot.api_key_source == "environment"
    assert snapshot.api_key.get_secret_value() == "environment-secret-9876"
    assert SettingsStore(manager._store.db_path).load().revision == 0


def test_new_environment_defaults_are_loaded_before_runtime_overrides(temp_dir):
    environment = _environment(
        LLM_PROVIDER="openai_compatible",
        LLM_TEMPERATURE=0.8,
        LLM_MAX_TOKENS=4096,
        LLM_TIMEOUT_SECONDS=45,
        DENSE_CANDIDATE_K=30,
        MAX_CHUNKS_PER_DOCUMENT=2,
    )

    snapshot = _manager(temp_dir, environment=environment).snapshot()

    assert snapshot.llm.provider == "openai_compatible"
    assert snapshot.llm.temperature == 0.8
    assert snapshot.llm.max_tokens == 4096
    assert snapshot.llm.timeout_seconds == 45
    assert snapshot.retrieval.dense_candidate_k == 30
    assert snapshot.retrieval.max_chunks_per_document == 2


def test_disabled_llm_allows_snapshot_without_any_api_key(temp_dir):
    environment = _environment(
        LLM_ENABLED=False,
        DEEPSEEK_API_KEY="",
    )

    snapshot = _manager(temp_dir, environment=environment).snapshot()

    assert snapshot.llm.enabled is False
    assert snapshot.api_key is None
    assert snapshot.api_key_source == "none"


def test_enabled_llm_rejects_snapshot_without_api_key(temp_dir):
    environment = _environment(DEEPSEEK_API_KEY="")

    with pytest.raises(RuntimeSettingsValidationError, match="API Key"):
        _manager(temp_dir, environment=environment)


def test_enabled_llm_can_restart_with_dpapi_key_and_no_environment_key(
    temp_dir,
):
    environment = _environment(DEEPSEEK_API_KEY="")

    snapshot = _manager(
        temp_dir,
        environment=environment,
        secret_value="dpapi-only-secret-2468",
    ).snapshot()

    assert snapshot.llm.enabled is True
    assert snapshot.api_key_source == "runtime_secret"
    assert snapshot.api_key.get_secret_value() == "dpapi-only-secret-2468"


def test_clearing_only_key_while_llm_enabled_is_atomic(temp_dir):
    environment = _environment(DEEPSEEK_API_KEY="")
    manager = _manager(
        temp_dir,
        environment=environment,
        secret_value="required-runtime-secret-1357",
    )

    with pytest.raises(RuntimeSettingsValidationError, match="API Key"):
        manager.update_llm(
            {},
            expected_revision=0,
            api_key_action="clear",
        )

    assert manager.snapshot().revision == 0
    assert (
        manager._secret_store.get_secret("llm_api_key")
        == "required-runtime-secret-1357"
    )


def test_non_secret_override_persists_across_manager_restart(temp_dir):
    manager = _manager(temp_dir)
    updated = manager.update(
        {
            "llm": {"model": "new-model", "temperature": 0.1},
            "retrieval": {
                "final_top_k": 8,
                "dense_candidate_k": 20,
                "reranker_candidate_k": 20,
                "max_chunks_per_document": 4,
            },
        },
        expected_revision=0,
    )

    restarted = _manager(temp_dir).snapshot()

    assert updated.revision == 1
    assert restarted.revision == 1
    assert restarted.llm.model == "new-model"
    assert restarted.retrieval.final_top_k == 8
    assert restarted.field_sources["llm.model"] == "runtime"
    assert restarted.field_sources["retrieval.bm25_k1"] == "environment"


def test_generation_controls_persist_with_independent_answer_and_rewrite_values(
    temp_dir,
):
    manager = _manager(temp_dir)

    updated, changed = manager.update_llm(
        {
            "answer_thinking_mode": "enabled",
            "answer_reasoning_effort": "max",
            "temperature": 0.7,
            "max_tokens": 4096,
            "rewrite_enabled": False,
            "rewrite_thinking_mode": "provider_default",
            "rewrite_reasoning_effort": "low",
            "rewrite_temperature": 0.2,
            "rewrite_max_tokens": 300,
            "rewrite_timeout_seconds": 18,
        },
        expected_revision=0,
    )

    restarted = _manager(temp_dir).snapshot()
    assert updated.llm.answer_thinking_mode == "enabled"
    assert restarted.llm.answer_reasoning_effort == "max"
    assert restarted.llm.temperature == 0.7
    assert restarted.llm.max_tokens == 4096
    assert restarted.llm.rewrite_enabled is False
    assert restarted.llm.rewrite_thinking_mode == "provider_default"
    assert restarted.llm.rewrite_reasoning_effort == "low"
    assert restarted.llm.rewrite_temperature == 0.2
    assert restarted.llm.rewrite_max_tokens == 300
    assert restarted.llm.rewrite_timeout_seconds == 18
    assert "llm.answer_thinking_mode" in changed
    assert "llm.rewrite_max_tokens" in changed
    assert restarted.field_sources["llm.rewrite_max_tokens"] == "runtime"


def test_reset_settings_restores_sections_and_runtime_key_atomically(temp_dir):
    manager = _manager(temp_dir, secret_value="runtime-secret-2468")
    manager.update_settings(
        {
            "llm": {"model": "runtime-model"},
            "retrieval": {
                "final_top_k": 8,
                "dense_candidate_k": 20,
                "bm25_top_k": 20,
                "reranker_candidate_k": 20,
                "max_chunks_per_document": 4,
            },
        },
        expected_revision=0,
    )

    retrieval_reset, retrieval_changes = manager.reset_settings(
        "retrieval",
        expected_revision=1,
    )

    assert retrieval_reset.revision == 2
    assert retrieval_reset.llm.model == "runtime-model"
    assert retrieval_reset.retrieval.final_top_k == 5
    assert retrieval_reset.api_key_source == "runtime_secret"
    assert "retrieval.final_top_k" in retrieval_changes

    llm_reset, llm_changes = manager.reset_settings(
        "llm",
        expected_revision=2,
    )

    assert llm_reset.revision == 3
    assert llm_reset.llm.model == "deepseek-v4-flash"
    assert llm_reset.api_key_source == "environment"
    assert manager._secret_store.get_secret("llm_api_key") is None
    assert "llm.model" in llm_changes
    assert "llm.api_key" in llm_changes


def test_reset_llm_rejects_missing_deployment_key_without_changes(temp_dir):
    manager = _manager(
        temp_dir,
        environment=_environment(DEEPSEEK_API_KEY=""),
        secret_value="required-runtime-secret-1357",
    )
    manager.update_settings(
        {"llm": {"model": "runtime-model"}},
        expected_revision=0,
    )

    with pytest.raises(RuntimeSettingsValidationError, match="API Key"):
        manager.reset_settings("llm", expected_revision=1)

    assert manager.snapshot().revision == 1
    assert manager.snapshot().llm.model == "runtime-model"
    assert (
        manager._secret_store.get_secret("llm_api_key")
        == "required-runtime-secret-1357"
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"storage": {"metadata_db_path": "other.db"}},
        {"retrieval": {"embedding_device": "cuda"}},
        {"retrieval": {"chunk_size": 1000}},
        {"llm": {"api_key": "forbidden"}},
    ],
)
def test_non_whitelist_fields_are_rejected_without_revision_change(temp_dir, patch):
    manager = _manager(temp_dir)

    with pytest.raises(RuntimeSettingsValidationError):
        manager.update(patch, expected_revision=0)

    assert manager.snapshot().revision == 0
    assert manager._store.load().revision == 0


def test_sensitive_unknown_field_does_not_echo_value_in_exception(temp_dir):
    manager = _manager(temp_dir)
    secret = "must-not-appear-in-validation-error"

    with pytest.raises(RuntimeSettingsValidationError) as error:
        manager.update({"llm": {"api_key": secret}}, expected_revision=0)

    assert secret not in str(error.value)
    assert error.value.__cause__ is None


def test_cross_field_validation_is_atomic(temp_dir):
    manager = _manager(temp_dir)

    with pytest.raises(RuntimeSettingsValidationError):
        manager.update(
            {
                "retrieval": {
                    "final_top_k": 12,
                    "dense_candidate_k": 10,
                    "reranker_candidate_k": 11,
                }
            },
            expected_revision=0,
        )

    assert manager.snapshot().revision == 0
    assert manager._store.load().revision == 0


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.example.com/v1?token=secret",
        "https://api.example.com/v1#fragment",
    ],
)
def test_runtime_base_url_rejects_query_and_fragment_atomically(
    temp_dir,
    base_url,
):
    manager = _manager(temp_dir)

    with pytest.raises(RuntimeSettingsValidationError):
        manager.update(
            {"llm": {"base_url": base_url}},
            expected_revision=0,
        )

    assert manager.snapshot().revision == 0
    assert manager._store.load().revision == 0


def test_stale_manager_cannot_overwrite_new_revision(temp_dir):
    first = _manager(temp_dir)
    stale = _manager(temp_dir)
    first.update({"llm": {"model": "winner"}}, expected_revision=0)

    with pytest.raises(SettingsRevisionConflict) as error:
        stale.update({"llm": {"model": "stale"}}, expected_revision=0)

    assert error.value.actual_revision == 1
    assert stale.snapshot().revision == 0
    assert first._store.load().payload["llm"]["model"] == "winner"


def test_runtime_secret_is_excluded_from_snapshot_dump_repr_and_sqlite(temp_dir):
    secret = "runtime-secret-should-not-leak-4321"
    manager = _manager(temp_dir, secret_value=secret)
    snapshot = manager.snapshot()

    assert snapshot.api_key_source == "runtime_secret"
    assert "api_key" not in snapshot.model_dump()
    assert secret not in repr(snapshot)
    manager.update({"llm": {"model": "safe-model"}}, expected_revision=0)
    raw_payload = manager._store.load().payload
    assert secret not in str(raw_payload)
    assert "api_key" not in str(raw_payload)


def test_snapshot_is_frozen_and_refusal_gate_cannot_be_enabled(temp_dir):
    manager = _manager(temp_dir)

    with pytest.raises(ValidationError):
        manager.snapshot().llm.model = "mutated"
    with pytest.raises(RuntimeSettingsValidationError):
        manager.update(
            {"retrieval": {"refusal_gate": {"enabled": True}}},
            expected_revision=0,
        )

    assert manager.snapshot().retrieval.refusal_gate.available is False
    assert manager.snapshot().retrieval.refusal_gate.enabled is False


def test_llm_update_replaces_and_clears_runtime_key_with_revision(temp_dir):
    manager = _manager(temp_dir)

    replaced, changed = manager.update_llm(
        {"model": "runtime-model", "temperature": 0.1},
        expected_revision=0,
        api_key_action="replace",
        api_key_value="runtime-secret-2468",
    )

    assert replaced.revision == 1
    assert replaced.llm.model == "runtime-model"
    assert replaced.api_key_source == "runtime_secret"
    assert replaced.api_key.get_secret_value() == "runtime-secret-2468"
    assert changed == [
        "llm.model",
        "llm.temperature",
        "llm.api_key",
    ]
    assert "api_key" not in str(manager._store.load().payload)

    cleared, changed = manager.update_llm(
        {},
        expected_revision=1,
        api_key_action="clear",
    )

    assert cleared.revision == 2
    assert cleared.api_key_source == "environment"
    assert cleared.api_key.get_secret_value() == "environment-secret-9876"
    assert changed == ["llm.api_key"]


def test_stale_llm_update_cannot_change_winning_runtime_key(temp_dir):
    first = _manager(temp_dir)
    stale = _manager(temp_dir)
    first.update_llm(
        {},
        expected_revision=0,
        api_key_action="replace",
        api_key_value="winning-secret-1111",
    )

    with pytest.raises(SettingsRevisionConflict):
        stale.update_llm(
            {},
            expected_revision=0,
            api_key_action="replace",
            api_key_value="stale-secret-9999",
        )

    assert (
        first._secret_store.get_secret("llm_api_key")
        == "winning-secret-1111"
    )


def test_llm_update_restores_old_secret_when_sqlite_save_fails(
    temp_dir,
    monkeypatch,
):
    manager = _manager(temp_dir, secret_value="original-secret-1234")

    def fail_save(*args, **kwargs):
        raise RuntimeError("simulated sqlite failure")

    monkeypatch.setattr(manager._store, "save", fail_save)

    with pytest.raises(RuntimeError, match="simulated sqlite failure"):
        manager.update_llm(
            {"model": "never-published"},
            expected_revision=0,
            api_key_action="replace",
            api_key_value="replacement-secret-5678",
        )

    assert manager.snapshot().revision == 0
    assert manager.snapshot().llm.model == "deepseek-v4-flash"
    assert (
        manager._secret_store.get_secret("llm_api_key")
        == "original-secret-1234"
    )
