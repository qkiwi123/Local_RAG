"""视觉配置的继承、密钥事务和草稿语义；外部服务在此全部替换。"""
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.main import app
from backend.models.settings_schemas import VisionSettingsPatch, VisionConnectionTestResponse
from backend.services.runtime_settings import RuntimeSettingsValidationError
from backend.services.settings_status import build_settings_response
from backend.services.settings_store import SettingsRevisionConflict
from tests.test_runtime_settings import _manager, _environment


def test_follow_uses_live_llm_connection_but_not_generation_controls(temp_dir):
    manager = _manager(temp_dir)
    first, _ = manager.update_settings({"vision": {"enabled": True, "recognize_max_tokens": 6000}}, expected_revision=0)
    second, _ = manager.update_settings({"llm": {"enabled": False, "model": "new-vision-model", "temperature": 1.2}}, expected_revision=1)
    view = build_settings_response(second)
    assert view.vision.effective_connection.model == "new-vision-model"
    assert view.vision.effective_connection.api_key.configured
    assert view.vision.enabled and not view.llm.enabled
    assert view.vision.recognize_temperature == 0
    assert view.vision.recognize_max_tokens == 6000
    assert first.llm.model != second.llm.model
    assert view.field_effects["vision.source"] == "next_document_upload"
    assert _manager(temp_dir).snapshot().vision.recognize_max_tokens == 6000


def test_custom_key_is_independent_masked_and_restorable(temp_dir):
    environment = _environment(VISION_API_KEY="environment-vision-1234")
    manager = _manager(temp_dir, environment=environment, secret_value="llm-secret-5678")
    snapshot, _ = manager.update_settings(
        {"vision": {"source": "custom", "enabled": True, "base_url": "https://vision.example/v1", "model": "custom-model"}},
        expected_revision=0, vision_api_key_action="replace", vision_api_key_value="custom-vision-9012",
    )
    view = build_settings_response(snapshot)
    assert view.vision.effective_connection.model == "custom-model"
    assert view.vision.api_key.masked_suffix == "9012"
    assert snapshot.api_key.get_secret_value() == "llm-secret-5678"
    assert "custom-vision-9012" not in view.model_dump_json()
    assert "custom-vision-9012" not in json.dumps(manager._store.load().payload)
    assert "custom-vision-9012" not in repr(snapshot)
    reset, _ = manager.reset_settings("vision", expected_revision=1)
    assert reset.vision.source == "follow_llm"
    assert reset.vision_api_key.get_secret_value() == "environment-vision-1234"
    assert reset.api_key.get_secret_value() == "llm-secret-5678"


def test_both_keys_rollback_together_on_database_error(temp_dir, monkeypatch):
    manager = _manager(temp_dir, secret_value="old-llm-key")
    manager._secret_store.set_secret("vision_api_key", "old-vision-key")
    def fail(*args, **kwargs):
        raise RuntimeError("database unavailable")
    monkeypatch.setattr(manager._store, "save", fail)
    with pytest.raises(RuntimeError, match="database unavailable"):
        manager.update_settings({}, expected_revision=0,
            api_key_action="replace", api_key_value="new-llm-key",
            vision_api_key_action="replace", vision_api_key_value="new-vision-key")
    assert manager._secret_store.get_secret("llm_api_key") == "old-llm-key"
    assert manager._secret_store.get_secret("vision_api_key") == "old-vision-key"
    assert manager.snapshot().revision == 0


def test_stale_revision_cannot_replace_vision_key(temp_dir):
    manager = _manager(temp_dir)
    manager.update_settings({"vision": {"max_calls_per_document": 30}}, expected_revision=0)
    with pytest.raises(SettingsRevisionConflict):
        manager.update_settings({}, expected_revision=0, vision_api_key_action="replace", vision_api_key_value="forbidden-new-key")
    assert manager._secret_store.get_secret("vision_api_key") is None


def test_reset_all_clears_both_runtime_keys_but_keeps_environment(temp_dir):
    manager = _manager(temp_dir, environment=_environment(VISION_API_KEY="env-vision"))
    manager.update_settings({}, expected_revision=0,
        api_key_action="replace", api_key_value="runtime-llm",
        vision_api_key_action="replace", vision_api_key_value="runtime-vision")
    snapshot, changed = manager.reset_settings("all", expected_revision=1)
    assert snapshot.api_key_source == "environment"
    assert snapshot.vision_api_key_source == "environment"
    assert manager._secret_store.get_secret("vision_api_key") is None
    assert manager._secret_store.get_secret("llm_api_key") is None
    assert {"llm.api_key", "vision.api_key"} <= set(changed)


def test_sensitive_root_validation_error_does_not_echo_draft_key():
    with TestClient(app) as client:
        response = client.patch("/api/settings", json={"expected_revision": 0, "vision": {
            "max_image_edge": None, "api_key_update": {"action": "replace", "value": "private-draft-secret"}}})
    assert response.status_code == 422
    assert "private-draft-secret" not in response.text


def test_validation_uses_effective_key_including_follow_when_llm_disabled(temp_dir):
    manager = _manager(temp_dir, environment=_environment(LLM_ENABLED=False, DEEPSEEK_API_KEY=""))
    with pytest.raises(RuntimeSettingsValidationError, match="API Key"):
        manager.update_settings({"vision": {"enabled": True}}, expected_revision=0)
    with pytest.raises(RuntimeSettingsValidationError, match="Base URL"):
        manager.update_settings({"vision": {"source": "custom", "enabled": True}}, expected_revision=0)
    manager.update_settings({"vision": {"source": "custom", "enabled": True, "base_url": "https://vision.example"}}, expected_revision=0,
        vision_api_key_action="replace", vision_api_key_value="runtime-vision-key")
    assert manager.snapshot().vision_api_key_source == "runtime_secret"


def test_draft_clear_uses_environment_key_and_never_saves(temp_dir):
    manager = _manager(temp_dir, environment=_environment(VISION_API_KEY="env-vision"))
    manager.update_settings({"vision": {"source": "custom", "base_url": "https://vision.example"}}, expected_revision=0,
        vision_api_key_action="replace", vision_api_key_value="saved-vision")
    draft = manager.vision_draft_snapshot(VisionSettingsPatch(api_key_update={"action": "clear"}))
    assert draft.vision_api_key.get_secret_value() == "env-vision"
    assert draft.vision.enabled
    assert manager.snapshot().vision_api_key.get_secret_value() == "saved-vision"
    assert not manager.snapshot().vision.enabled
    assert manager._store.load().revision == 1


@pytest.mark.parametrize("patch", [{"max_image_edge": 0}, {"recognize_max_tokens": 0}, {"source": "unknown"}, {"describe_temperature": None}, {"base_url": "https://user:secret@example.com"}])
def test_vision_patch_rejects_invalid_fields(patch):
    with pytest.raises(ValidationError):
        VisionSettingsPatch.model_validate(patch)


def test_api_save_read_test_and_reset_preserve_secret_boundaries(temp_dir, monkeypatch):
    from backend.services import vision
    manager = _manager(temp_dir)
    calls = []
    def probe(config, *, task):
        calls.append((config, task))
        return VisionConnectionTestResponse(success=True, latency_ms=1, provider="deepseek", model="draft-model", endpoint="https://api.deepseek.com", message="图片识别成功")
    monkeypatch.setattr(vision, "test_vision_connection", probe)
    with TestClient(app) as client:
        monkeypatch.setattr(app.state, "runtime_settings_manager", manager)
        response = client.patch("/api/settings", json={"expected_revision": 0, "vision": {
            "source": "custom", "base_url": "https://vision.example", "model": "saved-model", "api_key_update": {"action": "replace", "value": "saved-vision-key"}}})
        assert response.status_code == 200, response.text
        assert "saved-vision-key" not in response.text
        assert response.json()["settings"]["vision"]["api_key"]["configured"]
        probe_response = client.post("/api/settings/test-vision", json={"vision": {"model": "draft-model", "api_key_update": {"action": "clear"}}})
        assert probe_response.status_code == 422  # no fallback key; must not silently use saved key
        assert not calls
        assert client.post("/api/settings/test-vision", json={"vision": {"source": "follow_llm"}}).status_code == 200
        assert len(calls) == 1
        assert manager.snapshot().vision.model == "saved-model"
        assert manager.snapshot().revision == 1
        assert client.post("/api/settings/reset", json={"expected_revision": 1, "section": "vision"}).status_code == 200
