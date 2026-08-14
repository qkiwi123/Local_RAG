"""Streamlit 页面状态回归测试。"""

import httpx
from streamlit.testing.v1 import AppTest
from urllib.parse import unquote

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
    tags = []
    put_calls = []
    patch_calls = []
    delete_calls = []
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

    @staticmethod
    def _assert_absolute(url):
        if not (url.startswith("http://") or url.startswith("https://")):
            raise AssertionError(f"前端请求必须使用绝对 URL，收到: {url}")

    def get(self, url):
        self._assert_absolute(url)
        if url.endswith("/api/documents"):
            return FakeResponse(
                {
                    "documents": [item["original_name"] for item in self.documents],
                    "document_items": list(self.documents),
                }
            )
        if url.endswith("/api/tags"):
            return FakeResponse(list(self.tags))
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

    def post(self, url, files=None, json=None, data=None):
        self._assert_absolute(url)
        if url.endswith("/api/tags"):
            tag_path = json["tag_path"]
            item = {
                "tag_path": tag_path,
                "name": tag_path.rsplit("/", 1)[-1],
                "document_count": 0,
            }
            self.tags.append(item)
            return FakeResponse(item)
        if url.endswith("/api/upload"):
            name = files["file"][0]
            self.uploaded_names.append(name)
            self.documents.append(
                {
                    "document_id": "doc-test-1",
                    "original_name": name,
                    "status": "ready",
                    "chunk_count": 1,
                    "tags": list((data or {}).get("tags") or []),
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

    def put(self, url, json=None):
        self._assert_absolute(url)
        self.put_calls.append((url, json))
        document_id = url.rstrip("/").split("/")[-2]
        for item in self.documents:
            if item.get("document_id") == document_id:
                item["tags"] = list(json["tags"])
        return FakeResponse(
            {"status": "success", "document_id": document_id, "tags": json["tags"]}
        )

    def patch(self, url, json=None):
        self._assert_absolute(url)
        self.patch_calls.append((url, json))
        old_path = unquote(url.rstrip("/").split("/")[-1])
        new_path = json["new_path"]
        renamed = []
        for item in self.tags:
            path = item["tag_path"]
            if path == old_path or path.startswith(old_path + "/"):
                item["tag_path"] = new_path + path[len(old_path):]
                item["name"] = item["tag_path"].rsplit("/", 1)[-1]
                renamed.append(item["tag_path"])
        return FakeResponse({"status": "success", "renamed": renamed})

    def delete(self, url):
        self._assert_absolute(url)
        self.delete_calls.append(url)
        old_path = unquote(url.rstrip("/").split("/")[-1])
        self.tags[:] = [
            item
            for item in self.tags
            if item["tag_path"] != old_path
            and not item["tag_path"].startswith(old_path + "/")
        ]
        return FakeResponse({"status": "success"})


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


def _reset_backend():
    FakeBackendClient.uploaded_names = []
    FakeBackendClient.documents = []
    FakeBackendClient.tags = []
    FakeBackendClient.put_calls = []
    FakeBackendClient.patch_calls = []
    FakeBackendClient.delete_calls = []
    FakeBackendClient.conversation_messages = []


def _find_button(app, label_part):
    return next(button for button in app.button if label_part in button.label)


def test_upload_requires_explicit_confirm_button(monkeypatch):
    _reset_backend()
    monkeypatch.setattr(httpx, "Client", FakeBackendClient)

    app = AppTest.from_file("frontend/app.py", default_timeout=10)
    app.run()
    app.file_uploader[0].set_value(("sample.txt", b"hello", "text/plain"))

    app.run()

    # 选择文件只暂存，不自动上传
    assert not app.exception
    assert FakeBackendClient.uploaded_names == []
    assert len(app.session_state["_staged_files"]) == 1
    assert app.session_state["_uploader_generation"] == 0

    _find_button(app, "确认上传").click()
    app.run()

    # 确认后才提交；成功后清空暂存并轮换上传控件
    assert FakeBackendClient.uploaded_names == ["sample.txt"]
    assert not app.session_state["_staged_files"]
    assert app.file_uploader[0].value == []
    assert app.session_state["_uploader_generation"] == 1


def test_upload_carries_selected_tags(monkeypatch):
    _reset_backend()
    FakeBackendClient.tags = [
        {"tag_path": "课程课件", "name": "课程课件", "document_count": 0}
    ]
    monkeypatch.setattr(httpx, "Client", FakeBackendClient)

    app = AppTest.from_file("frontend/app.py", default_timeout=10)
    app.run()
    app.file_uploader[0].set_value(("sample.txt", b"hello", "text/plain"))
    app.run()
    app.multiselect(key="upload_tag_selection").set_value(["课程课件"])
    app.run()

    _find_button(app, "确认上传").click()
    app.run()

    assert FakeBackendClient.documents[0]["tags"] == ["课程课件"]


def test_creating_tag_auto_selects_it_for_upload(monkeypatch):
    _reset_backend()
    FakeBackendClient.tags = [
        {"tag_path": "已有标签", "name": "已有标签", "document_count": 0}
    ]
    monkeypatch.setattr(httpx, "Client", FakeBackendClient)

    app = AppTest.from_file("frontend/app.py", default_timeout=10)
    app.run()
    app.multiselect(key="upload_tag_selection").set_value(["已有标签"])
    app.run()

    next(
        item for item in app.text_input if item.label.startswith("新标签路径")
    ).set_value("课程课件/数据结构")
    next(button for button in app.button if button.label == "➕ 新建标签").click()
    app.run()

    assert not app.exception
    assert any(
        tag["tag_path"] == "课程课件/数据结构"
        for tag in FakeBackendClient.tags
    )
    # 新建后应合并保留已有选择，而不是覆盖
    assert app.session_state["upload_tag_selection"] == [
        "已有标签",
        "课程课件/数据结构",
    ]
    assert app.multiselect(key="upload_tag_selection").value == [
        "已有标签",
        "课程课件/数据结构",
    ]


def test_upload_area_create_tag_auto_selects_without_widget_error(monkeypatch):
    """上传区的「新建并选中」位于 multiselect 之后，创建后仍应自动选中且不报错。"""
    _reset_backend()
    FakeBackendClient.tags = [
        {"tag_path": "已有标签A", "name": "已有标签A", "document_count": 0},
        {"tag_path": "已有标签B", "name": "已有标签B", "document_count": 0},
    ]
    monkeypatch.setattr(httpx, "Client", FakeBackendClient)

    app = AppTest.from_file("frontend/app.py", default_timeout=10)
    app.run()
    app.multiselect(key="upload_tag_selection").set_value(
        ["已有标签A", "已有标签B"]
    )
    app.run()

    next(
        item for item in app.text_input if item.label.startswith("上传时新建标签")
    ).set_value("课程课件/数据结构")
    next(button for button in app.button if button.label == "➕ 新建并选中").click()
    app.run()

    assert not app.exception
    assert any(
        tag["tag_path"] == "课程课件/数据结构"
        for tag in FakeBackendClient.tags
    )
    # 已有选择必须保留，新标签追加进选中集
    assert app.session_state["upload_tag_selection"] == [
        "已有标签A",
        "已有标签B",
        "课程课件/数据结构",
    ]
    assert app.multiselect(key="upload_tag_selection").value == [
        "已有标签A",
        "已有标签B",
        "课程课件/数据结构",
    ]


def test_editing_existing_document_tags(monkeypatch):
    _reset_backend()
    FakeBackendClient.documents = [
        {
            "document_id": "doc-1",
            "original_name": "doc.txt",
            "status": "ready",
            "chunk_count": 1,
            "tags": ["旧标签"],
        }
    ]
    FakeBackendClient.tags = [
        {"tag_path": "旧标签", "name": "旧标签", "document_count": 1},
        {"tag_path": "新标签", "name": "新标签", "document_count": 0},
    ]
    monkeypatch.setattr(httpx, "Client", FakeBackendClient)

    app = AppTest.from_file("frontend/app.py", default_timeout=10)
    app.run()

    next(button for button in app.button if button.label == "🏷").click()
    app.run()

    select = app.multiselect(key="edit_tags_select_doc-1")
    assert select.value == ["旧标签"]
    select.set_value(["新标签"])
    next(
        button for button in app.button if button.label == "保存标签"
    ).click()
    app.run()

    assert not app.exception
    assert FakeBackendClient.put_calls == [
        (
            "http://127.0.0.1:8000/api/documents/id/doc-1/tags",
            {"tags": ["新标签"]},
        )
    ]
    assert FakeBackendClient.documents[0]["tags"] == ["新标签"]
    assert app.session_state["_edit_tags_for"] is None


def test_cancelling_tag_edit_closes_without_requests(monkeypatch):
    _reset_backend()
    FakeBackendClient.documents = [
        {
            "document_id": "doc-1",
            "original_name": "doc.txt",
            "status": "ready",
            "chunk_count": 1,
            "tags": ["旧标签"],
        }
    ]
    monkeypatch.setattr(httpx, "Client", FakeBackendClient)

    app = AppTest.from_file("frontend/app.py", default_timeout=10)
    app.run()

    next(button for button in app.button if button.label == "🏷").click()
    app.run()
    assert app.session_state["_edit_tags_for"] == "doc-1"

    next(button for button in app.button if button.label == "取消").click()
    app.run()

    assert not app.exception
    assert app.session_state["_edit_tags_for"] is None
    assert FakeBackendClient.put_calls == []
    assert FakeBackendClient.documents[0]["tags"] == ["旧标签"]


def test_rename_tag_from_management_ui(monkeypatch):
    from urllib.parse import quote

    _reset_backend()
    FakeBackendClient.tags = [
        {"tag_path": "旧分类", "name": "旧分类", "document_count": 1},
        {"tag_path": "旧分类/子类", "name": "子类", "document_count": 1},
    ]
    monkeypatch.setattr(httpx, "Client", FakeBackendClient)

    app = AppTest.from_file("frontend/app.py", default_timeout=10)
    app.run()

    app.selectbox(key="tag_mgmt_selected").set_value("旧分类")
    next(
        item for item in app.text_input if item.label.startswith("重命名为")
    ).set_value("新分类")
    next(button for button in app.button if button.label == "✏️ 重命名").click()
    app.run()

    assert not app.exception
    assert FakeBackendClient.patch_calls == [
        (
            "http://127.0.0.1:8000/api/tags/" + quote("旧分类", safe=""),
            {"new_path": "新分类"},
        )
    ]
    assert {tag["tag_path"] for tag in FakeBackendClient.tags} == {
        "新分类",
        "新分类/子类",
    }


def test_delete_tag_requires_confirmation(monkeypatch):
    from urllib.parse import quote

    _reset_backend()
    FakeBackendClient.tags = [
        {"tag_path": "待删除", "name": "待删除", "document_count": 0}
    ]
    monkeypatch.setattr(httpx, "Client", FakeBackendClient)

    app = AppTest.from_file("frontend/app.py", default_timeout=10)
    app.run()

    app.selectbox(key="tag_mgmt_selected").set_value("待删除")
    next(button for button in app.button if button.label == "🗑️ 删除").click()
    app.run()

    assert not app.exception
    assert FakeBackendClient.delete_calls == []
    assert app.session_state["_confirm_delete_tag"] == "待删除"

    next(button for button in app.button if button.label == "确定删除").click()
    app.run()

    assert FakeBackendClient.delete_calls == [
        "http://127.0.0.1:8000/api/tags/" + quote("待删除", safe="")
    ]
    assert FakeBackendClient.tags == []
    assert app.session_state["_confirm_delete_tag"] is None


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
