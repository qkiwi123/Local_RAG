import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from backend.services.conversation_store import (
    ConversationError,
    begin_chat,
    complete_chat,
    create_conversation,
    get_conversation,
    recover_interrupted_requests,
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
