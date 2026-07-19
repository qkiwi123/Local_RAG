"""Streamlit 页面状态回归测试。"""

import httpx
from streamlit.testing.v1 import AppTest

from frontend.trace_view import candidate_rows, executed_stage_names


LONG_CHUNK = "这是用于检索过程可视化的完整候选原文。" * 12
TRACE_PAYLOAD = {
    "retrieval_mode": "dense",
    "reranker_enabled": True,
    "chunks": {
        "c1": {
            "chunk_id": "c1",
            "document_name": "doc.txt",
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
        return FakeResponse(self.chat_payload)


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


def test_successful_upload_clears_file_uploader(monkeypatch):
    FakeBackendClient.uploaded_names = []
    FakeBackendClient.documents = []
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

    trace_expanders = [
        item for item in app.expander if "检索详情" in item.label
    ]
    assert len(trace_expanders) == 1
    assert trace_expanders[0].proto.expanded is False
    assert [tab.label for tab in trace_expanders[0].tabs] == [
        "Dense",
        "Reranker",
        "最终上下文",
    ]
    assert app.session_state.messages[-1]["trace"] == TRACE_PAYLOAD
