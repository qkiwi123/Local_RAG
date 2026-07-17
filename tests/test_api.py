"""FastAPI 集成测试。

外部 LLM 和 Embedding/向量写入均被 Mock；文件与 SQLite 使用 conftest 创建的
隔离目录，因此默认测试不消耗 API 额度，也不会接触生产数据。
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, isolated_data_dir):
    from backend.config import settings
    from backend.routers import chat as chat_router
    from backend.routers import upload as upload_router
    from backend.main import app

    db_path = Path(settings.METADATA_DB_PATH)
    if db_path.exists():
        db_path.unlink()
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    for path in upload_dir.iterdir():
        if path.is_file():
            path.unlink()

    monkeypatch.setattr(upload_router, "add_chunks", lambda chunks: len(chunks))
    monkeypatch.setattr(upload_router, "delete_document_by_id", lambda document_id: True)
    monkeypatch.setattr(upload_router, "get_legacy_document_names", lambda: [])

    retrieval = [
        {
            "chunk_id": "doc-1_p1_c0",
            "content": "Employees receive 5 days of annual leave.",
            "metadata": {
                "document_id": "doc-1",
                "document_name": "handbook.pdf",
                "page_number": 1,
                "chunk_index": 0,
            },
            "distance": 0.2,
        }
    ]
    monkeypatch.setattr(chat_router, "retrieve", lambda question: retrieval)
    monkeypatch.setattr(
        chat_router,
        "ask",
        lambda question, context: "Employees receive 5 days of annual leave.【来源1】",
    )

    with TestClient(app) as test_client:
        yield test_client


def _upload_fixture(client, fixture_name="sample.txt", upload_name=None):
    path = Path("tests/fixtures") / fixture_name
    with path.open("rb") as file_obj:
        return client.post(
            "/api/upload",
            files={
                "file": (
                    upload_name or fixture_name,
                    file_obj,
                    "application/octet-stream",
                )
            },
        )


class TestUploadAPI:
    def test_upload_returns_stable_document_id(self, client):
        from backend.config import settings
        from backend.services.document_store import get_document

        response = _upload_fixture(client)

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == "success"
        assert data["document_name"] == "sample.txt"
        assert len(data["document_id"]) == 32
        assert data["chunk_count"] > 0

        record = get_document(data["document_id"])
        assert record["status"] == "ready"
        assert record["original_name"] == "sample.txt"
        assert record["stored_name"] == f"{data['document_id']}.txt"
        assert (Path(settings.UPLOAD_DIR) / record["stored_name"]).is_file()

    def test_same_name_creates_independent_versions(self, client):
        first = _upload_fixture(client, upload_name="制度.txt")
        second = _upload_fixture(client, upload_name="制度.txt")

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["document_id"] != second.json()["document_id"]

        payload = client.get("/api/documents").json()
        versions = [
            item for item in payload["document_items"]
            if item["original_name"] == "制度.txt"
        ]
        assert len(versions) == 2

    def test_failed_ingest_cleans_files_and_marks_record(self, client, monkeypatch):
        from backend.config import settings
        from backend.routers import upload as upload_router
        from backend.services.document_store import list_documents

        deleted_ids = []
        monkeypatch.setattr(
            upload_router,
            "add_chunks",
            lambda chunks: (_ for _ in ()).throw(RuntimeError("向量写入失败")),
        )
        monkeypatch.setattr(
            upload_router,
            "delete_document_by_id",
            lambda document_id: deleted_ids.append(document_id) or True,
        )

        response = _upload_fixture(client)

        assert response.status_code == 500
        records = list_documents(status=None)
        assert len(records) == 1
        assert records[0]["status"] == "failed"
        assert "向量写入失败" in records[0]["error"]
        assert deleted_ids == [records[0]["document_id"]]
        assert list(Path(settings.UPLOAD_DIR).iterdir()) == []

    def test_delete_by_document_id(self, client):
        from backend.config import settings
        from backend.services.document_store import get_document

        uploaded = _upload_fixture(client).json()
        document_id = uploaded["document_id"]
        record = get_document(document_id)
        stored_path = Path(settings.UPLOAD_DIR) / record["stored_name"]

        response = client.delete(f"/api/documents/id/{document_id}")

        assert response.status_code == 200
        assert get_document(document_id) is None
        assert not stored_path.exists()

    def test_rejects_unsupported_format(self, client):
        response = client.post(
            "/api/upload",
            files={"file": ("malware.exe", b"not executable", "application/octet-stream")},
        )
        assert response.status_code == 400
        assert "不支持的文件格式" in response.json()["detail"]

    def test_rejects_path_in_filename(self):
        from backend.routers.upload import _validate_filename

        with pytest.raises(Exception, match="文件名不能包含路径"):
            _validate_filename("../outside.txt")
        with pytest.raises(Exception, match="文件名不能包含路径"):
            _validate_filename("folder\\outside.txt")

    def test_list_documents_has_legacy_and_structured_fields(self, client):
        _upload_fixture(client)

        response = client.get("/api/documents")

        assert response.status_code == 200
        data = response.json()
        assert data["documents"] == ["sample.txt"]
        assert data["document_items"][0]["document_id"]


class TestChatAPI:
    def test_chat_uses_mocked_llm_and_returns_grounded_citation(self, client):
        response = client.post(
            "/api/chat",
            json={"question": "How many days of annual leave do employees get?"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "5 days" in data["answer"]
        assert data["citations"] == [
            {
                "source_id": 1,
                "chunk_id": "doc-1_p1_c0",
                "document_name": "handbook.pdf",
                "page_number": 1,
                "excerpt": "Employees receive 5 days of annual leave.",
            }
        ]

    def test_chat_empty_question_returns_422(self, client):
        response = client.post("/api/chat", json={"question": ""})
        assert response.status_code == 422
