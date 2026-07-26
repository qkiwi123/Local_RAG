"""Streamlit 页面状态回归测试。"""

import httpx
from streamlit.testing.v1 import AppTest

from frontend.trace_view import (
    candidate_rows,
    executed_stage_names,
    remember_candidate_selection,
    selected_candidate_from_state,
)


LONG_CHUNK = "这是用于检索过程可视化的完整候选原文。" * 12
TRACE_PAYLOAD = {
    "retrieval_mode": "dense",
    "reranker_enabled": True,
    "chunks": {
        "c1": {
            "chunk_id": "c1",
            "document_name": "doc.txt",
            "location_label": "第 1 页",
            "page_number": 1,
            "content": LONG_CHUNK,
        }
    },
    "stages": [
        {
            "name": "dense",
            "candidates": [
                {
                    "chunk_id": "c1",
                    "rank": 1,
                    "distance": 0.2,
                    "dense_rank": 1,
                    "bm25_rank": None,
                    "bm25_score": None,
                    "rrf_score": None,
                    "reranker_score": None,
                    "eligible": True,
                    "filtered_reason": None,
                    "selected": True,
                }
            ],
        },
        {
            "name": "reranker",
            "candidates": [
                {
                    "chunk_id": "c1",
                    "rank": 1,
                    "distance": 0.2,
                    "dense_rank": 1,
                    "bm25_rank": None,
                    "bm25_score": None,
                    "rrf_score": None,
                    "reranker_score": 2.5,
                    "eligible": True,
                    "filtered_reason": None,
                    "selected": True,
                }
            ],
        },
        {
            "name": "selected",
            "candidates": [
                {
                    "chunk_id": "c1",
                    "rank": 1,
                    "distance": 0.2,
                    "dense_rank": 1,
                    "bm25_rank": None,
                    "bm25_score": None,
                    "rrf_score": None,
                    "reranker_score": 2.5,
                    "eligible": True,
                    "filtered_reason": None,
                    "selected": True,
                }
            ],
        },
    ],
    "selected_chunk_ids": ["c1"],
    "context": {"chunk_count": 1, "char_count": 240, "token_count": 96},
    "timings_ms": {
        "query_embedding": 10.0,
        "dense_retrieval": 5.0,
        "sparse_retrieval": 0.0,
        "rrf": 0.0,
        "rerank": 20.0,
        "context_formatting": 1.0,
        "llm_generation": 30.0,
        "total": 66.0,
    },
}


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self._payload


class FakeBackendClient:
    uploaded_names = []
    documents = []
    chat_payload = {"answer": "", "citations": [], "trace": None}
    conversations = [
        {
            "conversation_id": "conversation-1",
            "title": "测试对话",
            "created_at": "2026-07-26T00:00:00+00:00",
            "updated_at": "2026-07-26T00:00:00+00:00",
        }
    ]
    conversation_messages = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def get(self, url):
        if url.endswith("/api/documents"):
            return FakeResponse(
                {
                    "documents": [item["original_name"] for item in self.documents],
                    "document_items": list(self.documents),
                }
            )
        if url.endswith("/api/conversations"):
            return FakeResponse(list(self.conversations))
        if "/api/conversations/" in url:
            conversation_id = url.rsplit("/", 1)[-1]
            conversation = next(
                item for item in self.conversations
                if item["conversation_id"] == conversation_id
            )
            return FakeResponse(
                {**conversation, "messages": list(self.conversation_messages)}
            )
        return FakeResponse({"status": "ok"})

    def post(self, url, files=None, json=None):
        if url.endswith("/api/upload"):
            name = files["file"][0]
            self.uploaded_names.append(name)
            self.documents.append(
                {
                    "document_id": "doc-test-1",
                    "original_name": name,
                    "status": "ready",
                    "chunk_count": 1,
                }
            )
            return FakeResponse({"status": "success"})
        if url.endswith("/api/conversations"):
            conversation = {
                "conversation_id": "conversation-new",
                "title": "新对话",
                "created_at": "2026-07-26T00:00:00+00:00",
                "updated_at": "2026-07-26T00:00:00+00:00",
                "messages": [],
            }
            self.conversations.insert(0, {
                key: value for key, value in conversation.items()
                if key != "messages"
            })
            return FakeResponse(conversation)
        payload = {
            **self.chat_payload,
            "conversation_id": json["conversation_id"],
            "user_message_id": "user-1",
            "assistant_message_id": "assistant-1",
            "standalone_question": json["question"],
        }
        self.conversation_messages.extend([
            {
                "message_id": "user-1",
                "request_id": json["request_id"],
                "sequence": 0,
                "role": "user",
                "content": json["question"],
                "status": "complete",
                "standalone_question": json["question"],
                "citations": [],
                "created_at": "2026-07-26T00:00:00+00:00",
            },
            {
                "message_id": "assistant-1",
                "request_id": json["request_id"],
                "sequence": 1,
                "role": "assistant",
                "content": payload["answer"],
                "status": "complete",
                "standalone_question": None,
                "citations": payload["citations"],
                "created_at": "2026-07-26T00:00:01+00:00",
            },
        ])
        return FakeResponse(payload)


def test_candidate_rows_join_full_chunk_catalog_and_make_preview():
    rows = candidate_rows(TRACE_PAYLOAD, "dense")

    assert rows[0]["chunk_id"] == "c1"
    assert rows[0]["文档"] == "doc.txt"
    assert rows[0]["摘要"].endswith("…")
    assert rows[0]["是否入选"] == "是"


def test_executed_stage_names_follow_backend_order():
    assert executed_stage_names(TRACE_PAYLOAD) == [
        "dense",
        "reranker",
        "selected",
    ]


def test_selected_candidate_is_recovered_before_trace_panel_renders():
    session_state = {}

    changed = remember_candidate_selection(
        "message-1",
        "selected",
        [0],
        session_state,
    )

    selected = selected_candidate_from_state(
        TRACE_PAYLOAD,
        "message-1",
        session_state,
    )

    assert selected == {
        "stage_name": "selected",
        "row": candidate_rows(TRACE_PAYLOAD, "selected")[0],
    }
    assert changed is True
    assert remember_candidate_selection(
        "message-1",
        "selected",
        [0],
        session_state,
    ) is False


def test_successful_upload_clears_file_uploader(monkeypatch):
    FakeBackendClient.uploaded_names = []
    FakeBackendClient.documents = []
    FakeBackendClient.conversation_messages = []
    monkeypatch.setattr(httpx, "Client", FakeBackendClient)

    app = AppTest.from_file("frontend/app.py", default_timeout=10)
    app.run()
    app.file_uploader[0].set_value(("sample.txt", b"hello", "text/plain"))

    app.run()

    assert not app.exception
    assert FakeBackendClient.uploaded_names == ["sample.txt"]
    assert app.file_uploader[0].value == []
    assert app.session_state["_uploader_generation"] == 1


def test_trace_panel_is_present_and_collapsed_after_chat(monkeypatch):
    FakeBackendClient.conversation_messages = []
    FakeBackendClient.documents = [
        {
            "document_id": "doc-1",
            "original_name": "doc.txt",
            "status": "ready",
            "chunk_count": 1,
        }
    ]
    FakeBackendClient.chat_payload = {
        "answer": "回答【来源1】",
        "citations": [],
        "trace": TRACE_PAYLOAD,
    }
    monkeypatch.setattr(httpx, "Client", FakeBackendClient)
    app = AppTest.from_file("frontend/app.py", default_timeout=10)
    app.run()
    app.chat_input[0].set_value("问题")

    app.run()

    trace_buttons = [
        item for item in app.button if "检索详情" in item.label
    ]
    assert len(trace_buttons) == 1
    assert "展开" in trace_buttons[0].label
    assert not app.tabs

    trace_buttons[0].click()
    app.run()

    assert [tab.label for tab in app.tabs] == [
        "Dense",
        "Reranker",
        "最终上下文",
    ]
    assert app.session_state.messages[-1]["trace"] == TRACE_PAYLOAD


def test_first_question_automatically_creates_conversation(monkeypatch):
    monkeypatch.setattr(FakeBackendClient, "conversations", [])
    monkeypatch.setattr(FakeBackendClient, "conversation_messages", [])
    monkeypatch.setattr(
        FakeBackendClient,
        "documents",
        [
            {
                "document_id": "doc-1",
                "original_name": "doc.txt",
                "status": "ready",
                "chunk_count": 1,
            }
        ],
    )
    monkeypatch.setattr(
        FakeBackendClient,
        "chat_payload",
        {"answer": "自动创建后的回答", "citations": [], "trace": None},
    )
    monkeypatch.setattr(httpx, "Client", FakeBackendClient)

    app = AppTest.from_file("frontend/app.py", default_timeout=10)
    app.run()

    assert not app.chat_input[0].disabled
    assert app.session_state.current_conversation_id is None

    app.chat_input[0].set_value("首次直接提问")
    app.run()

    assert not app.exception
    assert app.session_state.current_conversation_id == "conversation-new"
    assert [message["content"] for message in app.session_state.messages] == [
        "首次直接提问",
        "自动创建后的回答",
    ]
    assert FakeBackendClient.conversations[0]["conversation_id"] == "conversation-new"
