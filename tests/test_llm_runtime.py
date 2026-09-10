"""S3 动态 LLM 客户端与连接测试；全部使用 fake client。"""

from types import SimpleNamespace

import pytest

from backend.services import llm
from backend.services.metadata_schema import bootstrap_structured_database
from backend.services.runtime_settings import RuntimeSettingsManager
from backend.services.secret_store import SecretStore
from tests.test_runtime_settings import _environment
from tests.test_secret_store import FakeProtector


def _manager(tmp_path):
    db_path = str(tmp_path / "metadata.db")
    bootstrap_structured_database(
        db_path,
        created_at="2026-08-24T00:00:00+00:00",
    )
    return RuntimeSettingsManager(
        db_path=db_path,
        environment=_environment(METADATA_DB_PATH=db_path),
        secret_store=SecretStore(
            tmp_path / "runtime_secrets.bin",
            protector=FakeProtector(),
        ),
    )


class _FakeCompletions:
    def __init__(self, *, error=None):
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if kwargs.get("stream"):
            return iter(
                [
                    SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(content="ok")
                            )
                        ]
                    )
                ]
            )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content="answer"))
            ]
        )


class _FakeClient:
    def __init__(self, *, error=None):
        self.completions = _FakeCompletions(error=error)
        self.chat = SimpleNamespace(completions=self.completions)


class _FakeAsyncStream:
    def __init__(self):
        self._pieces = iter(("异步", "流"))
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            text = next(self._pieces)
        except StopIteration as error:
            raise StopAsyncIteration from error
        return SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=text))]
        )

    async def close(self):
        self.closed = True


class _FakeAsyncCompletions:
    def __init__(self):
        self.calls = []
        self.streams = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        stream = _FakeAsyncStream()
        self.streams.append(stream)
        return stream


class _FakeAsyncClient:
    def __init__(self):
        self.completions = _FakeAsyncCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


@pytest.fixture(autouse=True)
def clear_client_cache():
    llm.clear_llm_client_cache()
    yield
    llm.clear_llm_client_cache()


def test_next_snapshot_uses_new_model_generation_and_timeout(
    tmp_path,
    monkeypatch,
):
    manager = _manager(tmp_path)
    created = []

    def fake_create(config):
        client = _FakeClient()
        created.append((config, client))
        return client

    monkeypatch.setattr(llm, "_create_client", fake_create)
    first = manager.snapshot()
    assert llm.ask("question", "context", first) == "answer"

    second, _ = manager.update_llm(
        {
            "model": "changed-model",
            "temperature": 0.8,
            "max_tokens": 321,
            "timeout_seconds": 17,
        },
        expected_revision=0,
    )
    assert llm.ask("question", "context", second) == "answer"

    assert len(created) == 2
    second_config, second_client = created[1]
    assert second_config.revision == 1
    assert second_config.timeout_seconds == 17
    call = second_client.completions.calls[0]
    assert call["model"] == "changed-model"
    assert call["temperature"] == 0.8
    assert call["max_tokens"] == 321
    assert "extra_body" not in call


@pytest.mark.asyncio
async def test_async_stream_uses_async_client_and_reuses_safe_cache_key(
    tmp_path,
    monkeypatch,
):
    manager = _manager(tmp_path)
    created = []

    def fake_create(config):
        client = _FakeAsyncClient()
        created.append((config, client))
        return client

    monkeypatch.setattr(llm, "_create_async_client", fake_create)
    runtime = manager.snapshot()

    first = [
        piece
        async for piece in llm.ask_stream_async(
            "question",
            "context",
            runtime,
        )
    ]
    second = [
        piece
        async for piece in llm.ask_stream_async(
            "question",
            "context",
            runtime,
        )
    ]

    assert first == ["异步", "流"]
    assert second == first
    assert len(created) == 1
    config, client = created[0]
    assert config.api_key.get_secret_value() not in repr(
        next(iter(llm._ASYNC_CLIENT_CACHE))
    )
    assert client.completions.calls[0]["stream"] is True
    assert all(stream.closed for stream in client.completions.streams)
    assert len(llm._ASYNC_CLIENT_CACHE) == 1


@pytest.mark.asyncio
async def test_async_stream_close_releases_upstream_response(
    tmp_path,
    monkeypatch,
):
    client = _FakeAsyncClient()
    monkeypatch.setattr(llm, "_create_async_client", lambda config: client)
    stream = llm.ask_stream_async(
        "question",
        "context",
        _manager(tmp_path).snapshot(),
    )

    assert await stream.__anext__() == "异步"
    await stream.aclose()

    assert client.completions.streams[0].closed is True


def test_followup_rewrite_disables_deepseek_thinking_and_tightens_prompt(
    tmp_path,
    monkeypatch,
):
    manager = _manager(tmp_path)
    client = _FakeClient()
    monkeypatch.setattr(llm, "_create_client", lambda config: client)

    rewritten = llm.rewrite_followup(
        "还有呢？",
        "上一轮内容",
        manager.snapshot(),
    )

    assert rewritten == "answer"
    call = client.completions.calls[0]
    assert call["temperature"] == 0
    assert call["max_tokens"] == 200
    assert call["extra_body"] == {"thinking": {"type": "disabled"}}
    system_prompt = call["messages"][0]["content"]
    assert "只补齐当前追问中省略的对象、条件或指代" in system_prompt
    assert "不得把单一对象扩大成多个对象或新增比较" in system_prompt
    assert "当前追问已经完整时，原样输出" in system_prompt
    assert call["messages"][1]["content"] == (
        "对话历史：\n上一轮内容\n\n当前追问：还有呢？"
    )


def test_followup_rewrite_does_not_send_deepseek_extension_to_other_providers(
    tmp_path,
    monkeypatch,
):
    manager = _manager(tmp_path)
    runtime, _ = manager.update_llm(
        {
            "provider": "openai_compatible",
            "base_url": "https://example.com/v1",
        },
        expected_revision=0,
    )
    client = _FakeClient()
    monkeypatch.setattr(llm, "_create_client", lambda config: client)

    assert llm.rewrite_followup("还有呢？", "上一轮内容", runtime) == "answer"

    assert "extra_body" not in client.completions.calls[0]


class _StatusError(Exception):
    def __init__(self, status_code):
        super().__init__("upstream detail must stay private")
        self.status_code = status_code


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (_StatusError(401), "authentication_failed"),
        (_StatusError(403), "permission_denied"),
        (_StatusError(404), "model_not_found"),
        (TimeoutError("private timeout"), "timeout"),
        (ConnectionError("private network"), "network_error"),
        (RuntimeError("private unknown"), "upstream_error"),
    ],
)
def test_connection_probe_returns_stable_sanitized_errors(
    tmp_path,
    monkeypatch,
    error,
    expected_code,
):
    config = llm.call_config_from_snapshot(_manager(tmp_path).snapshot())
    monkeypatch.setattr(
        llm,
        "_create_client",
        lambda ignored: _FakeClient(error=error),
    )

    result = llm.test_llm_connection(config)

    assert result.success is False
    assert result.error_code == expected_code
    assert "private" not in result.message
    assert result.endpoint == "https://api.deepseek.com"


def test_connection_probe_is_minimal_and_does_not_fill_live_cache(
    tmp_path,
    monkeypatch,
):
    config = llm.call_config_from_snapshot(_manager(tmp_path).snapshot())
    client = _FakeClient()
    monkeypatch.setattr(llm, "_create_client", lambda ignored: client)

    result = llm.test_llm_connection(config)

    assert result.success is True
    assert client.completions.calls == [
        {
            "model": config.model,
            "messages": [{"role": "user", "content": "ping"}],
            "temperature": 0,
            "max_tokens": 1,
        }
    ]
    assert len(llm._CLIENT_CACHE) == 0


def test_disabled_config_rejects_accidental_direct_llm_call(tmp_path):
    manager = _manager(tmp_path)
    disabled, _ = manager.update_llm(
        {"enabled": False},
        expected_revision=0,
    )

    with pytest.raises(llm.LLMDisabledError):
        llm.ask("question", "context", disabled)

    assert len(llm._CLIENT_CACHE) == 0
