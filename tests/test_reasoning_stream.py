from types import SimpleNamespace

import pytest

from backend.services import llm
from tests.test_llm_runtime import _manager


class ReasoningStream:
    def __init__(self, answer):
        self.items = iter([
            SimpleNamespace(choices=[]),  # usage-only 分片没有 choices。
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
                reasoning_content="检查证据", content=None,
            ))]),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
                reasoning_content="得出结论", content=answer,
            ))]),
        ])
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.items)
        except StopIteration:
            raise StopAsyncIteration

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("with_history", [False, True])
@pytest.mark.parametrize("include_reasoning", [False, True])
async def test_upstream_reasoning_is_separate_and_legacy_text_stream_is_compatible(
    tmp_path, monkeypatch, with_history, include_reasoning,
):
    stream = ReasoningStream("答案")
    async def create(**kwargs):
        return stream
    monkeypatch.setattr(llm, "_get_async_client", lambda config: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    ))
    runtime = _manager(tmp_path).snapshot()
    pieces = llm.ask_with_history_stream_async("问题", "问题", "资料", "", runtime, include_reasoning=include_reasoning) if with_history else llm.ask_stream_async("问题", "资料", runtime, include_reasoning=include_reasoning)
    result = [piece async for piece in pieces]
    expected = [llm.ReasoningDelta("检查证据"), llm.ReasoningDelta("得出结论"), "答案"] if include_reasoning else ["答案"]
    assert result == expected
    assert stream.closed


@pytest.mark.asyncio
async def test_upstream_reasoning_only_raises_after_forwarding_and_closes(tmp_path, monkeypatch):
    stream = ReasoningStream(None)
    async def create(**kwargs):
        return stream
    monkeypatch.setattr(llm, "_get_async_client", lambda config: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    ))
    received = []
    with pytest.raises(llm.EmptyLLMResponseError, match="思考"):
        async for piece in llm.ask_stream_async("问题", "资料", _manager(tmp_path).snapshot(), include_reasoning=True):
            received.append(piece)
    assert len(received) == 2
    assert stream.closed
