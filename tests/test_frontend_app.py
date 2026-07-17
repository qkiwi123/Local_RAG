"""Streamlit 页面状态回归测试。"""

import httpx
from streamlit.testing.v1 import AppTest


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
        return FakeResponse({"answer": "", "citations": []})


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
