import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from backend.services.conversation_store import (
    ConversationError,
    begin_chat,
    complete_chat,
    create_conversation,
    delete_conversation,
    get_conversation,
    load_trace,
    recover_interrupted_requests,
    save_trace,
)
from backend.services.metadata_schema import bootstrap_structured_database


def _db(temp_dir):
    path = f"{temp_dir}/metadata.db"
    bootstrap_structured_database(
        path,
        created_at="2026-07-26T00:00:00+00:00",
    )
    return path


def test_pending_request_blocks_parallel_submission(temp_dir):
    db_path = _db(temp_dir)
    conversation_id = create_conversation(db_path=db_path)["conversation_id"]
    begin_chat(
        conversation_id=conversation_id,
        request_id="request-1",
        question="第一问",
        expected_last_message_id=None,
        retry_failed=False,
        db_path=db_path,
    )

    with pytest.raises(ConversationError) as captured:
        begin_chat(
            conversation_id=conversation_id,
            request_id="request-2",
            question="第二问",
            expected_last_message_id=None,
            retry_failed=False,
            db_path=db_path,
        )

    assert captured.value.code == "request_in_progress"


def test_complete_request_replays_saved_snapshot(temp_dir):
    db_path = _db(temp_dir)
    conversation_id = create_conversation(db_path=db_path)["conversation_id"]
    started = begin_chat(
        conversation_id=conversation_id,
        request_id="request-1",
        question="第一问",
        expected_last_message_id=None,
        retry_failed=False,
        db_path=db_path,
    )
    completed = complete_chat(
        request_id="request-1",
        standalone_question="第一问",
        answer="回答",
        citations=[],
        db_path=db_path,
    )

    replay = begin_chat(
        conversation_id=conversation_id,
        request_id="request-1",
        question="第一问",
        expected_last_message_id=None,
        retry_failed=False,
        db_path=db_path,
    )["replay"]

    assert replay["user_message_id"] == started["user_message_id"]
    assert replay["assistant_message_id"] == completed["assistant_message_id"]
    assert replay["answer"] == "回答"


def test_startup_recovery_marks_expired_pending_failed(temp_dir, monkeypatch):
    db_path = _db(temp_dir)
    conversation_id = create_conversation(db_path=db_path)["conversation_id"]
    begin_chat(
        conversation_id=conversation_id,
        request_id="request-1",
        question="问题",
        expected_last_message_id=None,
        retry_failed=False,
        db_path=db_path,
    )
    old = (
        datetime.now(timezone.utc) - timedelta(hours=1)
    ).isoformat(timespec="seconds")
    connection = sqlite3.connect(db_path)
    connection.execute(
        "UPDATE chat_requests SET updated_at = ? WHERE request_id = 'request-1'",
        (old,),
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(
        "backend.config.settings.CONVERSATION_PENDING_TIMEOUT_SECONDS",
        60,
    )

    assert recover_interrupted_requests(db_path=db_path) == 1
    detail = get_conversation(conversation_id, db_path=db_path)
    assert detail["messages"][0]["status"] == "failed"


# ─── Trace 持久化（schema v3） ────────────────────────────────────

TRACE_PAYLOAD = {
    "retrieval_mode": "dense",
    "reranker_enabled": False,
    "chunks": {"c1": {"chunk_id": "c1", "document_name": "doc.txt", "content": "原文"}},
    "stages": [],
    "selected_chunk_ids": [],
    "context": {"chunk_count": 1, "char_count": 2, "token_count": 1},
    "timings_ms": {"total": 100.0},
}


def _completed_chat(db_path, conversation_id, request_id="request-1"):
    detail = get_conversation(conversation_id, db_path=db_path)
    complete = [
        message
        for message in detail["messages"]
        if message["status"] == "complete"
    ]
    expected = complete[-1]["message_id"] if complete else None
    begin_chat(
        conversation_id=conversation_id,
        request_id=request_id,
        question="问题",
        expected_last_message_id=expected,
        retry_failed=False,
        db_path=db_path,
    )
    return complete_chat(
        request_id=request_id,
        standalone_question="问题",
        answer="回答",
        citations=[],
        db_path=db_path,
    )


def test_trace_save_load_and_attach_to_conversation(temp_dir):
    db_path = _db(temp_dir)
    conversation_id = create_conversation(db_path=db_path)["conversation_id"]
    completed = _completed_chat(db_path, conversation_id)
    assistant_id = completed["assistant_message_id"]

    save_trace(assistant_id, TRACE_PAYLOAD, db_path=db_path)

    assert load_trace(assistant_id, db_path=db_path) == TRACE_PAYLOAD
    detail = get_conversation(conversation_id, db_path=db_path)
    assistant_message = detail["messages"][1]
    assert assistant_message["trace"] == TRACE_PAYLOAD
    assert "trace" not in detail["messages"][0]


def test_trace_replay_includes_persisted_trace(temp_dir):
    db_path = _db(temp_dir)
    conversation_id = create_conversation(db_path=db_path)["conversation_id"]
    completed = _completed_chat(db_path, conversation_id)
    save_trace(completed["assistant_message_id"], TRACE_PAYLOAD, db_path=db_path)

    replay = begin_chat(
        conversation_id=conversation_id,
        request_id="request-1",
        question="问题",
        expected_last_message_id=None,
        retry_failed=False,
        db_path=db_path,
    )["replay"]

    assert replay["trace"] == TRACE_PAYLOAD


def test_trace_storage_lru_evicts_oldest_over_limit(temp_dir, monkeypatch):
    db_path = _db(temp_dir)
    conversation_id = create_conversation(db_path=db_path)["conversation_id"]
    first = _completed_chat(db_path, conversation_id, request_id="request-1")
    save_trace(first["assistant_message_id"], TRACE_PAYLOAD, db_path=db_path)
    single_size = sqlite3.connect(db_path).execute(
        "SELECT size_bytes FROM message_traces"
    ).fetchone()[0]
    # 把第一条时间拨旧，避免同秒写入时按随机 UUID 决胜导致测试抖动
    connection = sqlite3.connect(db_path)
    connection.execute(
        "UPDATE message_traces SET created_at = '2020-01-01T00:00:00+00:00'"
    )
    connection.commit()
    connection.close()
    # 上限只够容纳一条 trace，写入第二条时最旧的一条应被淘汰
    monkeypatch.setattr(
        "backend.config.settings.TRACE_STORAGE_MAX_BYTES",
        single_size + 10,
    )

    second = _completed_chat(db_path, conversation_id, request_id="request-2")
    save_trace(second["assistant_message_id"], TRACE_PAYLOAD, db_path=db_path)

    assert load_trace(first["assistant_message_id"], db_path=db_path) is None
    assert load_trace(second["assistant_message_id"], db_path=db_path) == (
        TRACE_PAYLOAD
    )


def test_trace_cascade_deleted_with_conversation(temp_dir):
    db_path = _db(temp_dir)
    conversation_id = create_conversation(db_path=db_path)["conversation_id"]
    completed = _completed_chat(db_path, conversation_id)
    save_trace(completed["assistant_message_id"], TRACE_PAYLOAD, db_path=db_path)

    assert delete_conversation(conversation_id, db_path=db_path) is True
    connection = sqlite3.connect(db_path)
    count = connection.execute(
        "SELECT count(*) FROM message_traces"
    ).fetchone()[0]
    connection.close()

    assert count == 0
