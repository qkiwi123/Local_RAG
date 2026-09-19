import json
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
    assert payload["field_effects"]["llm.model"] == "next_request"
    assert payload["field_effects"]["retrieval.final_top_k"] == "next_request"
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
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["model_warmup"]["status"] == "disabled"
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
    assert payload["index"]["sparse_backend"] == "rank_bm25_memory"
    assert payload["index"]["sparse_index_status"] == "ready"
    assert payload["index"]["sparse_index_directory"] == "sparse_index"
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


def test_s4_exposes_only_planned_settings_mutations():
    mutating_methods = {"POST", "PUT", "PATCH", "DELETE"}
    mutating_settings_routes = [
        (
            path,
            sorted(
                method.upper()
                for method in operations
                if method.upper() in mutating_methods
            ),
        )
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api/settings")
        and any(method.upper() in mutating_methods for method in operations)
    ]

    assert mutating_settings_routes == [
        ("/api/settings", ["PATCH"]),
        ("/api/settings/reset", ["POST"]),
        ("/api/settings/test-vision", ["POST"]),
        ("/api/settings/test-llm", ["POST"]),
    ]


def test_patch_llm_settings_replaces_key_without_leaking_it(
    tmp_path,
):
    manager = _isolated_manager(tmp_path)
    secret = "new-runtime-key-2468"

    with TestClient(app) as client:
        original = app.state.runtime_settings_manager
        app.state.runtime_settings_manager = manager
        try:
            response = client.patch(
                "/api/settings",
                json={
                    "expected_revision": 0,
                    "llm": {
                        "provider": "openai_compatible",
                        "base_url": "https://gateway.example.com/v1",
                        "model": "new-model",
                        "temperature": 0.2,
                        "max_tokens": 512,
                        "timeout_seconds": 15,
                        "answer_thinking_mode": "enabled",
                        "answer_reasoning_effort": "high",
                        "rewrite_enabled": False,
                        "rewrite_thinking_mode": "provider_default",
                        "rewrite_reasoning_effort": "low",
                        "rewrite_temperature": 0.2,
                        "rewrite_max_tokens": 300,
                        "rewrite_timeout_seconds": 18,
                        "api_key_update": {
                            "action": "replace",
                            "value": secret,
                        },
                    },
                },
            )
        finally:
            app.state.runtime_settings_manager = original

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["revision"] == 1
    assert payload["settings"]["llm"]["model"] == "new-model"
    assert payload["settings"]["llm"]["answer_thinking_mode"] == "enabled"
    assert payload["settings"]["llm"]["rewrite_enabled"] is False
    assert payload["settings"]["llm"]["rewrite_max_tokens"] == 300
    assert payload["settings"]["llm"]["api_key"] == {
        "configured": True,
        "source": "runtime",
        "masked_suffix": "2468",
    }
    assert payload["effects"] == [
        "next_document_upload",
        "llm_client_cache_cleared",
        "next_request",
    ]
    assert "llm.api_key" in payload["changed_fields"]
    assert secret not in response.text
    assert manager._secret_store.get_secret("llm_api_key") == secret
    assert secret not in str(manager._store.load().payload)


def test_patch_llm_settings_rejects_stale_revision_without_key_change(
    tmp_path,
):
    manager = _isolated_manager(tmp_path, runtime_secret="winner-key-1111")
    manager.update_llm(
        {"model": "winner"},
        expected_revision=0,
    )

    with TestClient(app) as client:
        original = app.state.runtime_settings_manager
        app.state.runtime_settings_manager = manager
        try:
            response = client.patch(
                "/api/settings",
                json={
                    "expected_revision": 0,
                    "llm": {
                        "api_key_update": {
                            "action": "replace",
                            "value": "loser-key-9999",
                        }
                    },
                },
            )
        finally:
            app.state.runtime_settings_manager = original

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "settings_revision_conflict"
    assert manager._secret_store.get_secret("llm_api_key") == "winner-key-1111"
    assert manager.snapshot().llm.model == "winner"


def test_patch_retrieval_settings_activates_next_request_and_clears_bm25(
    tmp_path,
    monkeypatch,
):
    from backend.services import sparse_retriever

    manager = _isolated_manager(tmp_path)
    clear_calls = []
    monkeypatch.setattr(
        sparse_retriever,
        "clear_cache",
        lambda: clear_calls.append("cleared"),
    )

    with TestClient(app) as client:
        original = app.state.runtime_settings_manager
        app.state.runtime_settings_manager = manager
        try:
            response = client.patch(
                "/api/settings",
                json={
                    "expected_revision": 0,
                    "retrieval": {
                        "mode": "hybrid",
                        "final_top_k": 6,
                        "dense_candidate_k": 18,
                        "dense_distance_threshold": 0.55,
                        "bm25_top_k": 12,
                        "bm25_k1": 1.8,
                        "bm25_b": 0.6,
                        "rrf_k": 45,
                        "reranker_enabled": True,
                        "reranker_candidate_k": 10,
                        "max_chunks_per_document": 2,
                    },
                },
            )
        finally:
            app.state.runtime_settings_manager = original

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["revision"] == 1
    assert payload["settings"]["retrieval"]["final_top_k"] == 6
    assert payload["settings"]["retrieval"]["dense_candidate_k"] == 18
    assert payload["settings"]["retrieval"]["bm25_top_k"] == 12
    assert payload["settings"]["retrieval"]["reranker_candidate_k"] == 10
    assert payload["effects"] == ["bm25_cache_cleared", "next_request"]
    assert clear_calls == ["cleared"]
    assert manager.snapshot().retrieval.rrf_k == 45


def test_patch_retrieval_cross_field_validation_is_atomic(tmp_path):
    manager = _isolated_manager(tmp_path)

    with TestClient(app) as client:
        original = app.state.runtime_settings_manager
        app.state.runtime_settings_manager = manager
        try:
            response = client.patch(
                "/api/settings",
                json={
                    "expected_revision": 0,
                    "llm": {"model": "must-not-be-persisted"},
                    "retrieval": {
                        "final_top_k": 16,
                        "dense_candidate_k": 20,
                        "bm25_top_k": 15,
                        "reranker_candidate_k": 20,
                    },
                },
            )
        finally:
            app.state.runtime_settings_manager = original

    assert response.status_code == 422
    assert manager.snapshot().revision == 0
    assert manager.snapshot().llm.model == settings.DEEPSEEK_MODEL
    assert manager._store.load().revision == 0


def test_reset_settings_endpoint_restores_all_runtime_overrides(tmp_path):
    manager = _isolated_manager(tmp_path, runtime_secret="runtime-key-2468")
    manager.update_settings(
        {
            "llm": {"model": "runtime-model"},
            "retrieval": {
                "final_top_k": 6,
                "dense_candidate_k": 18,
                "bm25_top_k": 18,
                "reranker_candidate_k": 18,
            },
        },
        expected_revision=0,
    )

    with TestClient(app) as client:
        original = app.state.runtime_settings_manager
        app.state.runtime_settings_manager = manager
        try:
            response = client.post(
                "/api/settings/reset",
                json={"expected_revision": 1, "section": "all"},
            )
        finally:
            app.state.runtime_settings_manager = original

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["revision"] == 2
    assert payload["settings"]["llm"]["model"] == settings.DEEPSEEK_MODEL
    assert (
        payload["settings"]["retrieval"]["final_top_k"]
        == settings.RETRIEVAL_TOP_K
    )
    assert payload["settings"]["llm"]["api_key"]["source"] == "environment"
    assert payload["effects"] == ["next_document_upload", "llm_client_cache_cleared", "next_request"]
    assert "已恢复使用环境变量 API Key" in payload["warnings"]
    assert any("下次上传" in warning for warning in payload["warnings"])
    assert manager._secret_store.get_secret("llm_api_key") is None


def test_invalid_api_key_validation_response_does_not_echo_value():
    oversized_secret = "must-not-leak-" * 700

    with TestClient(app) as client:
        response = client.patch(
            "/api/settings",
            json={
                "expected_revision": 0,
                "llm": {
                    "api_key_update": {
                        "action": "replace",
                        "value": oversized_secret,
                    }
                },
            },
        )

    assert response.status_code == 422
    assert oversized_secret not in response.text
    assert "must-not-leak" not in response.text


def test_connection_test_uses_unsaved_draft_and_keeps_revision(
    tmp_path,
    monkeypatch,
):
    from backend.models.settings_schemas import LLMConnectionTestResponse
    from backend.routers import settings as settings_router

    manager = _isolated_manager(tmp_path)
    captured = {}
    draft_key = "draft-only-secret-7531"

    def fake_probe(config):
        captured["config"] = config
        return LLMConnectionTestResponse(
            success=True,
            latency_ms=12.5,
            provider=config.provider,
            model=config.model,
            endpoint="https://draft.example.com",
            message="连接成功",
        )

    monkeypatch.setattr(settings_router, "test_llm_connection", fake_probe)
    with TestClient(app) as client:
        original = app.state.runtime_settings_manager
        app.state.runtime_settings_manager = manager
        try:
            response = client.post(
                "/api/settings/test-llm",
                json={
                    "llm": {
                        "provider": "openai_compatible",
                        "base_url": "https://draft.example.com/v1",
                        "model": "draft-model",
                        "api_key": draft_key,
                        "timeout_seconds": 9,
                    }
                },
            )
        finally:
            app.state.runtime_settings_manager = original

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert captured["config"].model == "draft-model"
    assert captured["config"].timeout_seconds == 9
    assert captured["config"].api_key.get_secret_value() == draft_key
    assert draft_key not in response.text
    assert manager.snapshot().revision == 0
    assert manager.snapshot().llm.model == settings.DEEPSEEK_MODEL


def _retrieval_with_trace():
    from backend.models.schemas import (
        RetrievalResponse,
        RetrievalTrace,
        RetrievalTraceCandidate,
        RetrievalTraceChunk,
        RetrievalTraceStage,
    )
    from backend.services.parsers import PARSER_VERSION

    row = {
        "chunk_id": "unit-1_s0_e35",
        "content": "员工每年享有 5 天带薪年假。",
        "metadata": {
            "document_id": "doc-1",
            "document_name": "handbook.pdf",
            "unit_id": "unit-1",
            "unit_index": 0,
            "locator_kind": "page",
            "location_label": "第 1 页",
            "page_number": 1,
            "heading_path": "[]",
            "chunk_index": 0,
            "parser_version": PARSER_VERSION,
            "content_type": "paragraph",
            "start_char": 0,
            "end_char": 35,
        },
        "distance": 0.2,
    }
    trace = RetrievalTrace(
        retrieval_mode="dense",
        reranker_enabled=False,
        chunks={
            row["chunk_id"]: RetrievalTraceChunk(
                chunk_id=row["chunk_id"],
                document_name="handbook.pdf",
                page_number=1,
                content=row["content"],
            )
        },
        stages=[
            RetrievalTraceStage(
                name="dense",
                candidates=[
                    RetrievalTraceCandidate(
                        chunk_id=row["chunk_id"],
                        rank=1,
                        distance=0.2,
                        selected=True,
                    )
                ],
            )
        ],
        selected_chunk_ids=[row["chunk_id"]],
    )
    return RetrievalResponse(results=[row], trace=trace)


def test_retrieval_only_sync_stream_and_followup_make_zero_llm_calls(
    tmp_path,
    monkeypatch,
    offline_token_counter,
):
    from backend.routers import chat as chat_router

    manager = _isolated_manager(tmp_path)
    manager.update_llm({"enabled": False}, expected_revision=0)

    def forbidden(*args, **kwargs):
        raise AssertionError("retrieval-only 模式不得调用 LLM")

    monkeypatch.setattr(
        chat_router,
        "retrieve_with_trace",
        lambda *args, **kwargs: _retrieval_with_trace(),
    )
    for name in (
        "ask",
        "ask_stream_async",
        "ask_with_history",
        "ask_with_history_stream_async",
        "rewrite_followup",
    ):
        monkeypatch.setattr(chat_router, name, forbidden)

    with TestClient(app) as client:
        original = app.state.runtime_settings_manager
        app.state.runtime_settings_manager = manager
        try:
            conversation_id = client.post(
                "/api/conversations",
                json={},
            ).json()["conversation_id"]
            first = client.post(
                "/api/chat",
                json={
                    "question": "年假几天？",
                    "conversation_id": conversation_id,
                    "request_id": "retrieval-only-1",
                    "expected_last_message_id": None,
                },
            )
            second = client.post(
                "/api/chat",
                json={
                    "question": "还有呢？",
                    "conversation_id": conversation_id,
                    "request_id": "retrieval-only-2",
                    "expected_last_message_id": first.json()[
                        "assistant_message_id"
                    ],
                },
            )
            streamed = client.post(
                "/api/chat/stream",
                json={"question": "年假几天？"},
            )
        finally:
            app.state.runtime_settings_manager = original

    for response in (first, second):
        assert response.status_code == 200, response.text
        payload = response.json()
        assert "生成模型已关闭" in payload["answer"]
        assert payload["citations"][0]["document_name"] == "handbook.pdf"
        assert payload["trace"]["timings_ms"]["llm_generation"] == 0
        assert payload["trace"]["timing_contract_version"] == "chat-v2"
        assert payload["trace"]["followup_rewrite_status"] == "not_applicable"
        assert payload["trace"]["timings_ms"]["followup_rewrite"] is None
        assert payload["trace"]["timings_ms"]["citation_extraction"] is not None
        assert payload["trace"]["timings_ms"]["persistence"] is not None
        assert payload["trace"]["settings_revision"] == 1
        llm_trace = payload["trace"]["llm"]
        assert llm_trace["called"] is False
        assert llm_trace["provider"] == settings.LLM_PROVIDER
        assert llm_trace["model"] == settings.DEEPSEEK_MODEL
        assert llm_trace["answer"]["called"] is False
        assert llm_trace["rewrite"]["called"] is False
        assert llm_trace["rewrite"]["thinking_mode"] == "disabled"
        assert "api_key" not in json.dumps(payload["trace"]).lower()
    assert second.json()["standalone_question"] == "还有呢？"

    events = [
        json.loads(line)
        for line in streamed.text.splitlines()
        if line.strip()
    ]
    assert streamed.status_code == 200
    assert [event["event"] for event in events] == [
        "meta",
        "delta",
        "citations",
        "done",
    ]
    assert "生成模型已关闭" in events[1]["text"]
    assert events[2]["citations"][0]["document_name"] == "handbook.pdf"
    assert events[-1]["trace"]["settings_revision"] == 1
    assert events[-1]["trace"]["llm"]["called"] is False
    assert events[-1]["trace"]["timings_ms"]["llm_generation"] == 0
    assert events[-1]["trace"]["timings_ms"]["persistence"] is None
    assert events[-1]["trace"]["timings_ms"]["time_to_first_delta"] is not None
    assert events[-1]["trace"]["timings_ms"]["delta_window"] == 0


def test_chat_endpoints_use_the_effective_retrieval_snapshot(
    tmp_path,
    monkeypatch,
    offline_token_counter,
):
    from backend.routers import chat as chat_router

    manager = _isolated_manager(tmp_path)
    manager.update_settings(
        {
            "llm": {"enabled": False},
            "retrieval": {
                "mode": "hybrid",
                "final_top_k": 4,
                "dense_candidate_k": 12,
                "bm25_top_k": 9,
                "reranker_candidate_k": 8,
            },
        },
        expected_revision=0,
    )
    captured = []

    def fake_retrieve(*args, **kwargs):
        captured.append(kwargs["runtime_config"])
        return _retrieval_with_trace()

    monkeypatch.setattr(chat_router, "retrieve_with_trace", fake_retrieve)

    with TestClient(app) as client:
        original = app.state.runtime_settings_manager
        app.state.runtime_settings_manager = manager
        try:
            response = client.post(
                "/api/chat",
                json={"question": "年假几天？"},
            )
            streamed = client.post(
                "/api/chat/stream",
                json={"question": "年假几天？"},
            )
        finally:
            app.state.runtime_settings_manager = original

    assert response.status_code == 200, response.text
    assert streamed.status_code == 200, streamed.text
    assert len(captured) == 2
    for runtime in captured:
        assert runtime.mode == "hybrid"
        assert runtime.final_top_k == 4
        assert runtime.dense_candidate_k == 12
        assert runtime.bm25_top_k == 9
        assert runtime.reranker_candidate_k == 8

    sync_trace = response.json()["trace"]
    stream_events = [
        json.loads(line)
        for line in streamed.text.splitlines()
        if line.strip()
    ]
    stream_trace = stream_events[-1]["trace"]
    for trace in (sync_trace, stream_trace):
        assert trace["settings_revision"] == 1
        assert trace["effective_config"]["retrieval_mode"] == "hybrid"
        assert trace["effective_config"]["final_top_k"] == 4
        assert trace["effective_config"]["dense_candidate_k"] == 12
        assert trace["effective_config"]["bm25_top_k"] == 9
        assert trace["effective_config"]["reranker_candidate_k"] == 8
        assert trace["llm"]["called"] is False


def test_chat_trace_distinguishes_settings_before_and_after_update(
    tmp_path,
    monkeypatch,
    offline_token_counter,
):
    from backend.routers import chat as chat_router

    manager = _isolated_manager(tmp_path)
    manager.update_llm({"enabled": False}, expected_revision=0)
    monkeypatch.setattr(
        chat_router,
        "retrieve_with_trace",
        lambda *args, **kwargs: _retrieval_with_trace(),
    )

    with TestClient(app) as client:
        original = app.state.runtime_settings_manager
        app.state.runtime_settings_manager = manager
        try:
            before = client.post(
                "/api/chat",
                json={"question": "修改前的设置版本？"},
            )
            manager.update_settings(
                {
                    "retrieval": {
                        "final_top_k": 3,
                        "dense_candidate_k": 7,
                        "dense_distance_threshold": 0.29,
                        "bm25_top_k": 6,
                        "reranker_candidate_k": 5,
                        "max_chunks_per_document": 2,
                    }
                },
                expected_revision=1,
            )
            after = client.post(
                "/api/chat",
                json={"question": "修改后的设置版本？"},
            )
        finally:
            app.state.runtime_settings_manager = original

    assert before.status_code == 200, before.text
    assert after.status_code == 200, after.text
    before_trace = before.json()["trace"]
    after_trace = after.json()["trace"]
    assert before_trace["settings_revision"] == 1
    assert after_trace["settings_revision"] == 2
    assert before_trace["effective_config"]["final_top_k"] != 3
    assert after_trace["effective_config"]["final_top_k"] == 3
    assert after_trace["effective_config"]["dense_candidate_k"] == 7
    assert after_trace["effective_config"]["dense_distance_threshold"] == 0.29
    assert "api_key" not in after.text.lower()
