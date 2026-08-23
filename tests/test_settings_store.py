import json
import sqlite3

import pytest

from backend.services.metadata_schema import bootstrap_structured_database
from backend.services.settings_store import (
    SettingsRevisionConflict,
    SettingsStore,
)


CREATED_AT = "2026-08-24T00:00:00+00:00"


def _store(temp_dir, *, history_limit=50):
    db_path = f"{temp_dir}/metadata.db"
    bootstrap_structured_database(db_path, created_at=CREATED_AT)
    return db_path, SettingsStore(db_path, history_limit=history_limit)


def test_missing_row_is_revision_zero_and_save_persists_non_secret_payload(temp_dir):
    db_path, store = _store(temp_dir)

    assert store.load().revision == 0
    saved = store.save(
        {"llm": {"model": "model-next"}},
        expected_revision=0,
        changed_keys=["llm.model"],
    )

    assert saved.revision == 1
    assert store.load().payload == {"llm": {"model": "model-next"}}
    connection = sqlite3.connect(db_path)
    try:
        raw = connection.execute(
            "SELECT payload_json FROM runtime_settings"
        ).fetchone()[0]
    finally:
        connection.close()
    assert json.loads(raw) == {"llm": {"model": "model-next"}}


def test_expected_revision_conflict_rolls_back_payload_and_history(temp_dir):
    _, store = _store(temp_dir)
    store.save(
        {"retrieval": {"final_top_k": 5}},
        expected_revision=0,
        changed_keys=["retrieval.final_top_k"],
    )

    with pytest.raises(SettingsRevisionConflict) as error:
        store.save(
            {"retrieval": {"final_top_k": 9}},
            expected_revision=0,
            changed_keys=["retrieval.final_top_k"],
        )

    assert error.value.actual_revision == 1
    assert store.load().payload["retrieval"]["final_top_k"] == 5
    assert [item["revision"] for item in store.history()] == [1]


def test_history_is_bounded_and_changed_keys_are_deduplicated(temp_dir):
    _, store = _store(temp_dir, history_limit=2)
    for revision, top_k in enumerate((5, 6, 7)):
        store.save(
            {"retrieval": {"final_top_k": top_k}},
            expected_revision=revision,
            changed_keys=["retrieval.final_top_k", "retrieval.final_top_k"],
        )

    history = store.history()
    assert [item["revision"] for item in history] == [3, 2]
    assert history[0]["changed_keys"] == ["retrieval.final_top_k"]


@pytest.mark.parametrize(
    "payload",
    [
        {"llm": {"api_key": "must-not-persist"}},
        {"llm": {"access_token": "must-not-persist"}},
        {"llm": {"bearer_token": "must-not-persist"}},
        {"nested": [{"password": "must-not-persist"}]},
    ],
)
def test_sensitive_fields_are_rejected_before_sqlite_write(temp_dir, payload):
    _, store = _store(temp_dir)

    with pytest.raises(ValueError, match="敏感字段"):
        store.save(payload, expected_revision=0, changed_keys=[])

    assert store.load().revision == 0
