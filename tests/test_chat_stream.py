"""流式问答端点测试：事件协议、重放、错误事件与中断方案甲（不访问真实 LLM）。"""

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(isolated_data_dir):
    from backend.config import settings
    from backend.main import app

    db_path = Path(settings.METADATA_DB_PATH)
    if db_path.exists():
        db_path.unlink()
    with TestClient(app) as test_client:
        yield test_client


def _events(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/x-ndjson")
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def _retrieval_row():
    from backend.services.parsers import PARSER_VERSION

    return {
        "chunk_id": "unit-1_s0_e49",
        "content": "Employees receive 5 days of annual leave.",
        "metadata": {
            "document_id": "doc-1",
            "document_name": "handbook.pdf",
            "unit_id": "unit-1",
            "unit_index": 0,
            "locator_kind": "page",
            "location_label": "第 1 页",
            "page_number": 1,
            "heading_path": "[]",
            "parser_version": PARSER_VERSION,
            "start_char": 0,
            "end_char": 49,
        },
        "distance": 0.2,
    }


def _mock_retrieval(monkeypatch, rows=None, raise_error=None):
    from backend.models.schemas import RetrievalResponse
    from backend.routers import chat as chat_router

    def fake(question, **kwargs):
        if raise_error is not None:
            raise raise_error
        return RetrievalResponse(results=list(rows or []), trace=None)

    monkeypatch.setattr(chat_router, "retrieve_with_trace", fake)


def _mock_stream(monkeypatch, pieces, raise_after=0):
    from backend.routers import chat as chat_router

    def fake_stream(*args, **kwargs):
        for index, piece in enumerate(pieces):
            if raise_after and index >= raise_after:
                raise RuntimeError("流中断")
            yield piece

    monkeypatch.setattr(chat_router, "ask_stream", fake_stream)
    monkeypatch.setattr(chat_router, "ask_with_history_stream", fake_stream)


def test_stream_event_sequence_and_answer_assembly(client, monkeypatch):
    _mock_retrieval(monkeypatch, rows=[_retrieval_row()])
    _mock_stream(monkeypatch, ["员工", "有", "5 天年假。"])

    response = client.post(
        "/api/chat/stream", json={"question": "年假几天？"}
    )

    events = _events(response)
    assert [event["event"] for event in events] == [
        "meta", "delta", "delta", "delta", "citations", "done",
    ]
    answer = "".join(
        event["text"] for event in events if event["event"] == "delta"
    )
    assert answer == "员工有5 天年假。"
    assert events[0]["standalone_question"] == "年假几天？"
    done = events[-1]
    assert done["assistant_message_id"] is None  # 非持久化无消息 ID
    assert done["trace"] is None


def test_stream_empty_retrieval_uses_fixed_message(client, monkeypatch):
    _mock_retrieval(monkeypatch, rows=[])

    response = client.post(
        "/api/chat/stream", json={"question": "无答案问题"}
    )

    events = _events(response)
    answer = "".join(
        event["text"] for event in events if event["event"] == "delta"
    )
    assert "未找到与您问题相关的文档内容" in answer
    assert events[-1]["event"] == "done"


def test_stream_tag_filter_empty_message_distinguishes_scope(client, monkeypatch):
    _mock_retrieval(monkeypatch, rows=[])
    # 标签「课程课件」无文档，筛选范围为空
    from backend.services.tag_store import create_tag

    create_tag("课程课件")

    response = client.post(
        "/api/chat/stream",
        json={"question": "问题", "tags": ["课程课件"]},
    )

    answer = "".join(
        event["text"] for event in _events(response) if event["event"] == "delta"
    )
    assert "当前筛选范围内未找到" in answer


def test_stream_replay_returns_saved_answer_in_one_shot(client, monkeypatch):
    from backend.routers import chat as chat_router

    _mock_retrieval(monkeypatch, rows=[_retrieval_row()])
    monkeypatch.setattr(
        chat_router,
        "ask_with_history",
        lambda question, standalone, context, history: "已保存回答【来源1】",
    )
    monkeypatch.setattr(
        chat_router,
        "rewrite_followup",
        lambda question, history: question,
    )
    conversation_id = client.post("/api/conversations", json={}).json()[
        "conversation_id"
    ]
    payload = {
        "question": "年假几天？",
        "conversation_id": conversation_id,
        "request_id": "stream-replay-1",
        "expected_last_message_id": None,
    }
    first = client.post("/api/chat", json=payload)
    assert first.status_code == 200

    replay_response = client.post("/api/chat/stream", json=payload)
    events = _events(replay_response)

    assert [event["event"] for event in events] == [
        "meta", "delta", "citations", "done",
    ]
    assert "".join(
        event["text"] for event in events if event["event"] == "delta"
    ) == "已保存回答【来源1】"
    assert events[-1]["assistant_message_id"] == first.json()[
        "assistant_message_id"
    ]


def test_stream_retrieval_error_emits_error_event(client, monkeypatch):
    _mock_retrieval(monkeypatch, raise_error=RuntimeError("向量库不可用"))

    response = client.post(
        "/api/chat/stream", json={"question": "问题"}
    )

    events = _events(response)
    assert events[0]["event"] == "meta"
    assert events[-1]["event"] == "error"
    assert events[-1]["code"] == "retrieval_error"


def test_stream_llm_error_emits_error_event(client, monkeypatch):
    _mock_retrieval(monkeypatch, rows=[_retrieval_row()])

    def failing_stream(*args, **kwargs):
        yield "开始"
        raise RuntimeError("模型不可用")

    from backend.routers import chat as chat_router

    monkeypatch.setattr(chat_router, "ask_stream", failing_stream)

    response = client.post(
        "/api/chat/stream", json={"question": "问题"}
    )

    events = _events(response)
    assert [event["event"] for event in events] == [
        "meta", "delta", "error",
    ]
    assert events[-1]["code"] == "llm_error"


def test_stream_interrupt_fails_chat_and_keeps_no_partial_answer(
    client, monkeypatch
):
    """方案甲：客户端中途断开 → fail_chat(interrupted)，不落半截答案。"""
    from backend.config import settings
    from backend.routers import chat as chat_router

    _mock_retrieval(monkeypatch, rows=[_retrieval_row()])

    def slow_stream(*args, **kwargs):
        for piece in ["半", "截", "答", "案"]:
            yield piece

    monkeypatch.setattr(chat_router, "ask_with_history_stream", slow_stream)
    monkeypatch.setattr(
        chat_router, "rewrite_followup", lambda question, history: question
    )
    conversation_id = client.post("/api/conversations", json={}).json()[
        "conversation_id"
    ]
    request = chat_router.ChatRequest(
        question="问题",
        conversation_id=conversation_id,
        request_id="stream-interrupt-1",
        expected_last_message_id=None,
        retry_failed=False,
    )
    started = chat_router.begin_chat(
        conversation_id=conversation_id,
        request_id=request.request_id,
        question="问题",
        expected_last_message_id=None,
        retry_failed=False,
    )

    async def consume_partial():
        generator = chat_router._stream_chat_events(
            request, started, "问题", [], None
        )
        first = await generator.__anext__()
        second = await generator.__anext__()
        await generator.aclose()
        return first, second

    first, second = asyncio.run(consume_partial())
    assert json.loads(first)["event"] == "meta"
    assert json.loads(second)["event"] == "delta"

    from backend.services.conversation_store import get_conversation

    detail = get_conversation(
        conversation_id, db_path=settings.METADATA_DB_PATH
    )
    messages = detail["messages"]
    assert len(messages) == 1  # 只有 user 消息，无 assistant 半截
    assert messages[0]["status"] == "failed"


def test_stream_protocol_errors_keep_http_status(client):
    response = client.post(
        "/api/chat/stream",
        json={
            "question": "问题",
            "conversation_id": "missing-conv",
            "request_id": "r-1",
            "expected_last_message_id": None,
        },
    )
    assert response.status_code == 404

    response = client.post("/api/chat/stream", json={"question": ""})
    assert response.status_code == 422

    response = client.post(
        "/api/chat/stream",
        json={"question": "问题", "tags": ["坏/标签/"]},
    )
    assert response.status_code == 422
