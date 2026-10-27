"""顺序会话覆盖与范围快照，全部在独立SQLite中验证。"""
import sqlite3

import pytest

from backend.services import conversation_store as store
from backend.services.metadata_schema import bootstrap_structured_database


@pytest.fixture
def history(tmp_path):
    db = str(tmp_path / "conversation.db")
    bootstrap_structured_database(db, created_at="before")
    conv = store.create_conversation(db_path=db)["conversation_id"]
    for n in range(3):
        messages = store.get_conversation(conv, db_path=db)["messages"]
        store.begin_chat(conversation_id=conv, request_id=f"r{n}", question=f"问题{n}",
                         expected_last_message_id=messages[-1]["message_id"] if messages else None,
                         retry_failed=False, tags=[f"范围{n}"], db_path=db)
        saved = store.complete_chat(request_id=f"r{n}", standalone_question=f"独立问题{n}",
                                    answer=f"答案{n}", citations=[], reasoning_content=f"思考{n}", db_path=db)
        store.save_trace(saved["assistant_message_id"], {"round": n}, db_path=db)
    return db, conv, store.get_conversation(conv, db_path=db)


def begin(history, request="edit", **changes):
    db, conv, detail = history
    args = dict(conversation_id=conv, request_id=request, question="修改后的问题",
                expected_last_message_id=detail["messages"][-1]["message_id"],
                retry_failed=False, tags=["新范围"], replace_message_id=detail["messages"][2]["message_id"], db_path=db)
    args.update(changes)
    return store.begin_chat(**args)


def test_edit_success_replaces_only_target_and_suffix_and_cleans_old_traces(history):
    db, conv, before = history
    started = begin(history)
    assert store.get_conversation(conv, db_path=db) == before
    assert [m["content"] for m in store.complete_history(conv, before_sequence=started["history_before_sequence"], db_path=db)] == ["问题0", "答案0"]
    saved = store.complete_chat(request_id="edit", standalone_question="新独立问题", answer="新答案",
                                citations=[], reasoning_content="新思考", db_path=db)
    after = store.get_conversation(conv, db_path=db)
    assert after["messages"][:2] == before["messages"][:2]
    assert len(after["messages"]) == 4
    assert after["messages"][2]["content"] == "修改后的问题"
    assert after["messages"][2]["retrieval_tags"] == ["新范围"]
    assert after["messages"][3]["reasoning_content"] == "新思考"
    assert saved["user_message_id"] == before["messages"][2]["message_id"]
    assert store.load_trace(before["messages"][3]["message_id"], db_path=db) is None
    assert store.load_trace(before["messages"][5]["message_id"], db_path=db) is None
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT request_id FROM chat_requests ORDER BY request_id").fetchall() == [("edit",), ("r0",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    replay = begin(history)["replay"]
    assert replay["answer"] == "新答案"


@pytest.mark.parametrize("error", ["interrupted", "llm_error", "empty_response"])
def test_failed_replacement_keeps_every_original_message_trace_and_scope(history, error):
    db, conv, before = history
    begin(history)
    store.fail_chat("edit", error, db_path=db)
    assert store.get_conversation(conv, db_path=db) == before
    begin(history, request="try-again")


def test_transaction_failure_rolls_back_deletion_and_message_edit(history):
    db, conv, before = history
    begin(history)
    with pytest.raises(AttributeError):
        store.complete_chat(request_id="edit", standalone_question="new", answer="new", citations=[object()], db_path=db)
    assert store.get_conversation(conv, db_path=db) == before


def test_stale_cross_conversation_and_assistant_targets_are_rejected(history):
    db, _, before = history
    with pytest.raises(store.ConversationError, match="末尾已变化"):
        begin(history, expected_last_message_id=before["messages"][1]["message_id"])
    with pytest.raises(store.ConversationError, match="不存在"):
        begin(history, replace_message_id=before["messages"][1]["message_id"])
    other = store.create_conversation(db_path=db)["conversation_id"]
    with pytest.raises(store.ConversationError, match="不存在"):
        begin(history, conversation_id=other)


def test_pending_replacement_blocks_appends_and_other_replacements(history):
    db, conv, before = history
    begin(history)
    with pytest.raises(store.ConversationError, match="正在处理"):
        begin(history, request="second")
    with pytest.raises(store.ConversationError, match="正在处理"):
        store.begin_chat(conversation_id=conv, request_id="append", question="追加",
                         expected_last_message_id=before["messages"][-1]["message_id"], retry_failed=False, db_path=db)


def test_idempotency_rejects_changed_question_or_scope(history):
    begin(history)
    with pytest.raises(store.ConversationError, match="不能变化"):
        begin(history, tags=[])
    with pytest.raises(store.ConversationError, match="不能变化"):
        begin(history, question="另一个问题")


def test_edit_first_round_uses_no_old_context_and_can_follow_up(history):
    db, conv, before = history
    started = begin(history, replace_message_id=before["messages"][0]["message_id"], tags=[])
    assert store.complete_history(conv, before_sequence=started["history_before_sequence"], db_path=db) == []
    saved = store.complete_chat(request_id="edit", standalone_question="new", answer="new", citations=[], db_path=db)
    assert store.get_conversation(conv, db_path=db)["messages"][0]["retrieval_tags"] == []
    store.begin_chat(conversation_id=conv, request_id="next", question="新追问",
                     expected_last_message_id=saved["assistant_message_id"], retry_failed=False, tags=[], db_path=db)
