"""本地单用户持久化对话存储与请求幂等控制。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from backend.config import settings
from backend.models.schemas import Citation
from backend.services.document_store import _connection, _now
from backend.services.metadata_schema import require_structured_database


class ConversationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _db_path(db_path: Optional[str]) -> str:
    value = db_path or settings.METADATA_DB_PATH
    require_structured_database(value)
    return value


def create_conversation(title: str | None = None, *, db_path=None) -> dict:
    conversation_id = uuid4().hex
    timestamp = _now()
    clean_title = (title or "新对话").strip()[:120] or "新对话"
    with _connection(_db_path(db_path)) as connection:
        connection.execute(
            "INSERT INTO conversations VALUES (?, ?, ?, ?)",
            (conversation_id, clean_title, timestamp, timestamp),
        )
    return get_conversation(conversation_id, db_path=db_path)


def list_conversations(*, db_path=None) -> list[dict]:
    with _connection(_db_path(db_path)) as connection:
        rows = connection.execute(
            "SELECT * FROM conversations ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def _decode_message(row) -> dict:
    item = dict(row)
    raw = item.pop("citations_json", None)
    item["citations"] = (
        [Citation.model_validate(value).model_dump() for value in json.loads(raw)]
        if raw else []
    )
    raw_tags = item.pop("retrieval_tags_json", None)
    item["retrieval_tags"] = json.loads(raw_tags) if raw_tags is not None else None
    item.pop("conversation_id", None)
    return item


def save_trace(
    message_id: str,
    trace: dict,
    *,
    db_path=None,
) -> None:
    """保存检索 Trace；总量超过上限时按创建时间淘汰最旧记录（LRU）。

    trace 为已序列化的 RetrievalTrace 字典；由调用方保证消息已 complete。
    """
    raw = json.dumps(trace, ensure_ascii=False, separators=(",", ":"))
    size = len(raw.encode("utf-8"))
    with _connection(_db_path(db_path)) as connection:
        connection.execute(
            """
            INSERT INTO message_traces (message_id, trace_json, size_bytes, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(message_id) DO UPDATE SET
                trace_json = excluded.trace_json,
                size_bytes = excluded.size_bytes,
                created_at = excluded.created_at
            """,
            (message_id, raw, size, _now()),
        )
        while True:
            total = connection.execute(
                "SELECT coalesce(sum(size_bytes), 0) FROM message_traces"
            ).fetchone()[0]
            if total <= settings.TRACE_STORAGE_MAX_BYTES:
                break
            connection.execute(
                """
                DELETE FROM message_traces
                WHERE message_id = (
                    SELECT message_id FROM message_traces
                    ORDER BY created_at ASC, message_id ASC
                    LIMIT 1
                )
                """
            )


def load_trace(message_id: str, *, db_path=None) -> dict | None:
    with _connection(_db_path(db_path)) as connection:
        row = connection.execute(
            "SELECT trace_json FROM message_traces WHERE message_id = ?",
            (message_id,),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["trace_json"])
    except ValueError:
        return None


def get_conversation(conversation_id: str, *, db_path=None) -> dict | None:
    with _connection(_db_path(db_path)) as connection:
        row = connection.execute(
            "SELECT * FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        if not row:
            return None
        messages = connection.execute(
            """
            SELECT * FROM messages
            WHERE conversation_id = ?
            ORDER BY sequence
            """,
            (conversation_id,),
        ).fetchall()
        trace_rows = connection.execute(
            """
            SELECT message_id, trace_json FROM message_traces
            WHERE message_id IN (
                SELECT message_id FROM messages WHERE conversation_id = ?
            )
            """,
            (conversation_id,),
        ).fetchall()
    traces = {
        trace_row["message_id"]: trace_row["trace_json"]
        for trace_row in trace_rows
    }
    result = dict(row)
    decoded = []
    for item in messages:
        message = _decode_message(item)
        if message["role"] == "assistant":
            raw_trace = traces.get(message["message_id"])
            try:
                message["trace"] = json.loads(raw_trace) if raw_trace else None
            except ValueError:
                message["trace"] = None
        decoded.append(message)
    result["messages"] = decoded
    return result


def rename_conversation(conversation_id: str, title: str, *, db_path=None) -> dict:
    clean = title.strip()
    if not clean:
        raise ConversationError("invalid_title", "标题不能为空")
    with _connection(_db_path(db_path)) as connection:
        cursor = connection.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE conversation_id = ?",
            (clean[:120], _now(), conversation_id),
        )
        if cursor.rowcount != 1:
            raise ConversationError("conversation_not_found", "会话不存在")
    return get_conversation(conversation_id, db_path=db_path)


def delete_conversation(conversation_id: str, *, db_path=None) -> bool:
    with _connection(_db_path(db_path)) as connection:
        cursor = connection.execute(
            "DELETE FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        )
    return cursor.rowcount == 1


def _saved_response(connection, request_id: str) -> dict:
    rows = connection.execute(
        """
        SELECT * FROM messages
        WHERE request_id = ?
        ORDER BY sequence
        """,
        (request_id,),
    ).fetchall()
    user = next(row for row in rows if row["role"] == "user")
    assistant = next(row for row in rows if row["role"] == "assistant")
    citations = json.loads(assistant["citations_json"] or "[]")
    trace_row = connection.execute(
        "SELECT trace_json FROM message_traces WHERE message_id = ?",
        (assistant["message_id"],),
    ).fetchone()
    trace = None
    if trace_row:
        try:
            trace = json.loads(trace_row["trace_json"])
        except ValueError:
            trace = None
    return {
        "conversation_id": user["conversation_id"],
        "user_message_id": user["message_id"],
        "assistant_message_id": assistant["message_id"],
        "standalone_question": user["standalone_question"] or user["content"],
        "answer": assistant["content"],
        "reasoning_content": assistant["reasoning_content"],
        "citations": citations,
        "trace": trace,
    }


def begin_chat(
    *,
    conversation_id: str,
    request_id: str,
    question: str,
    expected_last_message_id: str | None,
    retry_failed: bool,
    tags: list[str] | None = None,
    replace_message_id: str | None = None,
    db_path=None,
) -> dict:
    """原子校验会话尾部、幂等状态并预留 user sequence。"""
    path = _db_path(db_path)
    with _connection(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        conversation = connection.execute(
            "SELECT * FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        if not conversation:
            raise ConversationError("conversation_not_found", "会话不存在")

        existing = connection.execute(
            "SELECT * FROM chat_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if existing:
            if existing["conversation_id"] != conversation_id:
                raise ConversationError("request_id_conflict", "request_id 已被其他会话使用")
            replacement = connection.execute(
                "SELECT * FROM chat_replacements WHERE request_id = ?", (request_id,)
            ).fetchone()
            saved_user = connection.execute(
                "SELECT * FROM messages WHERE request_id = ? AND role = 'user'", (request_id,)
            ).fetchone()
            saved_question = replacement["question"] if replacement else saved_user["content"] if saved_user else None
            saved_tags = replacement["retrieval_tags_json"] if replacement else saved_user["retrieval_tags_json"] if saved_user else None
            if (saved_question != question
                    or (replacement["target_message_id"] if replacement else None) != replace_message_id
                    or (tags is not None and saved_tags is not None and json.loads(saved_tags) != tags)):
                raise ConversationError("request_id_conflict", "同一请求的内容和检索范围不能变化")
            if existing["status"] == "complete":
                return {"replay": _saved_response(connection, request_id)}
            if existing["status"] == "pending":
                raise ConversationError("request_in_progress", "请求正在处理中")
            if replacement:
                raise ConversationError("stale_retry", "请使用新的请求重新生成，原记录已保留")
            if not retry_failed:
                raise ConversationError("retry_required", "失败请求必须显式重试")
            user = connection.execute(
                "SELECT * FROM messages WHERE request_id = ? AND role = 'user'",
                (request_id,),
            ).fetchone()
            if user["content"] != question:
                raise ConversationError(
                    "request_id_conflict",
                    "同一 request_id 的问题内容不能变化",
                )
            later = connection.execute(
                "SELECT 1 FROM messages WHERE conversation_id = ? AND sequence > ? LIMIT 1",
                (conversation_id, user["sequence"]),
            ).fetchone()
            if later:
                raise ConversationError("stale_retry", "失败请求之后已有新消息")
            if connection.execute(
                "SELECT 1 FROM chat_requests WHERE conversation_id = ? AND status = 'pending'",
                (conversation_id,),
            ).fetchone():
                raise ConversationError("request_in_progress", "该会话已有请求正在处理中")
            connection.execute(
                """
                UPDATE chat_requests
                SET status = 'pending', error_code = NULL, updated_at = ?
                WHERE request_id = ?
                """,
                (_now(), request_id),
            )
            connection.execute(
                """
                UPDATE messages
                SET status = 'pending', standalone_question = NULL,
                    retrieval_tags_json = coalesce(retrieval_tags_json, ?)
                WHERE message_id = ?
                """,
                (json.dumps(tags, ensure_ascii=False) if tags is not None else None, user["message_id"]),
            )
            return {"user_message_id": user["message_id"]}

        pending = connection.execute(
            """
            SELECT 1 FROM chat_requests
            WHERE conversation_id = ? AND status = 'pending'
            LIMIT 1
            """,
            (conversation_id,),
        ).fetchone()
        if pending:
            raise ConversationError("request_in_progress", "该会话已有请求正在处理中")

        if replace_message_id:
            target = connection.execute(
                "SELECT * FROM messages WHERE message_id = ? AND conversation_id = ? AND role = 'user'",
                (replace_message_id, conversation_id),
            ).fetchone()
            if not target or target["status"] == "pending":
                raise ConversationError("invalid_replace_target", "该问题不存在或尚未处理完成，请刷新后重试")
            tail = connection.execute(
                "SELECT message_id FROM messages WHERE conversation_id = ? ORDER BY sequence DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
            if not tail or tail["message_id"] != expected_last_message_id:
                raise ConversationError("conversation_conflict", "会话末尾已变化，请刷新后重试")
            timestamp = _now()
            connection.execute(
                "INSERT INTO chat_requests VALUES (?, ?, ?, 'pending', NULL, ?, ?)",
                (request_id, conversation_id, expected_last_message_id, timestamp, timestamp),
            )
            connection.execute(
                "INSERT INTO chat_replacements VALUES (?, ?, ?, ?, ?)",
                (request_id, target["message_id"], target["request_id"], question,
                 json.dumps(tags or [], ensure_ascii=False)),
            )
            return {"user_message_id": target["message_id"], "history_before_sequence": target["sequence"]}

        last = connection.execute(
            """
            SELECT message_id FROM messages
            WHERE conversation_id = ? AND status = 'complete'
            ORDER BY sequence DESC LIMIT 1
            """,
            (conversation_id,),
        ).fetchone()
        actual_last = last["message_id"] if last else None
        if actual_last != expected_last_message_id:
            raise ConversationError("conversation_conflict", "会话末尾已变化，请刷新后重试")

        count = connection.execute(
            "SELECT count(1) FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()[0]
        if count >= settings.CONVERSATION_MAX_MESSAGES:
            raise ConversationError("conversation_limit", "当前会话已达到消息上限")
        next_sequence = connection.execute(
            "SELECT coalesce(max(sequence), -1) + 1 FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()[0]
        timestamp = _now()
        user_message_id = uuid4().hex
        connection.execute(
            """
            INSERT INTO chat_requests VALUES (?, ?, ?, 'pending', NULL, ?, ?)
            """,
            (
                request_id,
                conversation_id,
                expected_last_message_id,
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO messages (
                message_id, conversation_id, request_id, sequence, role,
                content, status, standalone_question, citations_json, created_at, retrieval_tags_json
            ) VALUES (?, ?, ?, ?, 'user', ?, 'pending', NULL, NULL, ?, ?)
            """,
            (
                user_message_id,
                conversation_id,
                request_id,
                next_sequence,
                question,
                timestamp,
                json.dumps(tags, ensure_ascii=False) if tags is not None else None,
            ),
        )
        if count == 0 and conversation["title"] == "新对话":
            connection.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE conversation_id = ?",
                (question[:40], timestamp, conversation_id),
            )
    return {"user_message_id": user_message_id}


def _commit_replacement(connection, request, replacement) -> None:
    """在生成成功的同一事务中校验并替换；此前原消息/Trace始终不动。"""
    target = connection.execute(
        "SELECT * FROM messages WHERE message_id = ? AND conversation_id = ? AND role = 'user'",
        (replacement["target_message_id"], request["conversation_id"]),
    ).fetchone()
    tail = connection.execute(
        "SELECT message_id FROM messages WHERE conversation_id = ? ORDER BY sequence DESC LIMIT 1",
        (request["conversation_id"],),
    ).fetchone()
    if (not target or target["request_id"] != replacement["target_request_id"]
            or not tail or tail["message_id"] != request["expected_last_message_id"]):
        raise ConversationError("conversation_conflict", "原会话已变化，未覆盖任何记录")
    # 同时清理被覆盖轮次的请求与失败暂存；保留本次请求用于完成重放。
    connection.execute(
        """DELETE FROM chat_requests WHERE conversation_id = ? AND request_id != ? AND (
            request_id IN (SELECT request_id FROM messages WHERE conversation_id = ? AND sequence >= ?)
            OR request_id IN (SELECT r.request_id FROM chat_replacements r JOIN messages m
                ON m.message_id = r.target_message_id WHERE m.conversation_id = ? AND m.sequence >= ?))""",
        (request["conversation_id"], request["request_id"], request["conversation_id"], target["sequence"],
         request["conversation_id"], target["sequence"]),
    )
    connection.execute(
        "DELETE FROM messages WHERE conversation_id = ? AND sequence > ?",
        (request["conversation_id"], target["sequence"]),
    )
    connection.execute(
        """UPDATE messages SET request_id = ?, content = ?, retrieval_tags_json = ?,
            status = 'pending', standalone_question = NULL WHERE message_id = ?""",
        (request["request_id"], replacement["question"], replacement["retrieval_tags_json"], target["message_id"]),
    )


def complete_chat(
    *,
    request_id: str,
    standalone_question: str,
    answer: str,
    citations: list[Citation],
    reasoning_content: str = "",
    db_path=None,
) -> dict:
    with _connection(_db_path(db_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        request = connection.execute(
            "SELECT * FROM chat_requests WHERE request_id = ?", (request_id,)
        ).fetchone()
        if not request or request["status"] != "pending":
            raise ConversationError("request_not_pending", "请求状态已变化")
        replacement = connection.execute(
            "SELECT * FROM chat_replacements WHERE request_id = ?", (request_id,)
        ).fetchone()
        if replacement:
            _commit_replacement(connection, request, replacement)
        user = connection.execute(
            "SELECT * FROM messages WHERE request_id = ? AND role = 'user'",
            (request_id,),
        ).fetchone()
        if not user or user["status"] != "pending":
            raise ConversationError("request_not_pending", "请求状态已变化")
        assistant_message_id = uuid4().hex
        timestamp = _now()
        connection.execute(
            """
            UPDATE messages
            SET status = 'complete', standalone_question = ?
            WHERE message_id = ?
            """,
            (standalone_question, user["message_id"]),
        )
        connection.execute(
            """
            INSERT INTO messages (
                message_id, conversation_id, request_id, sequence, role,
                content, status, standalone_question, citations_json, created_at,
                reasoning_content
            ) VALUES (
                ?, ?, ?, ?, 'assistant', ?, 'complete', NULL, ?, ?, ?
            )
            """,
            (
                assistant_message_id,
                user["conversation_id"],
                request_id,
                user["sequence"] + 1,
                answer,
                json.dumps(
                    [citation.model_dump(mode="json") for citation in citations],
                    ensure_ascii=False,
                ),
                timestamp,
                reasoning_content,
            ),
        )
        connection.execute(
            "UPDATE chat_requests SET status = 'complete', updated_at = ? WHERE request_id = ?",
            (timestamp, request_id),
        )
        connection.execute(
            "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
            (timestamp, user["conversation_id"]),
        )
    return {
        "conversation_id": user["conversation_id"],
        "user_message_id": user["message_id"],
        "assistant_message_id": assistant_message_id,
    }


def fail_chat(request_id: str, error_code: str, *, db_path=None) -> None:
    with _connection(_db_path(db_path)) as connection:
        timestamp = _now()
        connection.execute(
            """
            UPDATE chat_requests
            SET status = 'failed', error_code = ?, updated_at = ?
            WHERE request_id = ? AND status = 'pending'
            """,
            (error_code[:80], timestamp, request_id),
        )
        connection.execute(
            """
            UPDATE messages SET status = 'failed'
            WHERE request_id = ? AND role = 'user' AND status = 'pending'
            """,
            (request_id,),
        )


def complete_history(conversation_id: str, *, before_sequence: int | None = None, db_path=None) -> list[dict]:
    with _connection(_db_path(db_path)) as connection:
        rows = connection.execute(
            """
            SELECT role, content, message_id, sequence
            FROM messages
            WHERE conversation_id = ? AND status = 'complete' AND (? IS NULL OR sequence < ?)
            ORDER BY sequence
            """,
            (conversation_id, before_sequence, before_sequence),
        ).fetchall()
    return [dict(row) for row in rows]


def recover_interrupted_requests(*, db_path=None) -> int:
    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(seconds=settings.CONVERSATION_PENDING_TIMEOUT_SECONDS)
    ).isoformat(timespec="seconds")
    with _connection(_db_path(db_path)) as connection:
        rows = connection.execute(
            """
            SELECT request_id FROM chat_requests
            WHERE status = 'pending' AND updated_at < ?
            """,
            (cutoff,),
        ).fetchall()
        for row in rows:
            connection.execute(
                """
                UPDATE chat_requests
                SET status = 'failed', error_code = 'interrupted', updated_at = ?
                WHERE request_id = ?
                """,
                (_now(), row["request_id"]),
            )
            connection.execute(
                """
                UPDATE messages SET status = 'failed'
                WHERE request_id = ? AND role = 'user' AND status = 'pending'
                """,
                (row["request_id"],),
            )
    return len(rows)
