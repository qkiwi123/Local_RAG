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
    from backend.models.schemas import RetrievalResponse
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

    retrieval = [
        {
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
                "chunk_index": 0,
                "parser_version": "structured-v1",
                "content_type": "paragraph",
                "start_char": 0,
                "end_char": 49,
            },
            "distance": 0.2,
        }
    ]
    monkeypatch.setattr(
        chat_router,
        "retrieve_with_trace",
        lambda question: RetrievalResponse(results=retrieval, trace=None),
        raising=False,
    )
    monkeypatch.setattr(
        chat_router,
        "ask",
        lambda question, context: "Employees receive 5 days of annual leave.【来源1】",
    )

    with TestClient(app) as test_client:
        yield test_client


def _upload_fixture(client, fixture_name="sample.txt", upload_name=None):
    path = Path("tests/fixtures") / fixture_name
    return _upload_path(client, path, upload_name=upload_name)


def _upload_path(client, path: Path, upload_name=None):
    with path.open("rb") as file_obj:
        return client.post(
            "/api/upload",
            files={
                "file": (
                    upload_name or path.name,
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

    def test_upload_route_accepts_all_five_supported_formats(self, client):
        paths = [
            Path("tests/fixtures/sample.pdf"),
            Path("tests/fixtures/sample.docx"),
            Path("tests/fixtures/sample.txt"),
            Path("eval/corpus/会议纪要（测试文档）.md"),
            Path("eval/corpus/Chap01.pptx"),
        ]

        responses = [_upload_path(client, path) for path in paths]

        assert [response.status_code for response in responses] == [200] * 5
        assert {
            Path(response.json()["document_name"]).suffix.lower()
            for response in responses
        } == {".pdf", ".docx", ".txt", ".md", ".pptx"}

    def test_rejects_path_in_filename(self):
        from backend.routers.upload import _validate_filename

        with pytest.raises(Exception, match="文件名不能包含路径"):
            _validate_filename("../outside.txt")
        with pytest.raises(Exception, match="文件名不能包含路径"):
            _validate_filename("folder\\outside.txt")

    def test_list_documents_has_compatibility_names_and_structured_items(self, client):
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
                "chunk_id": "unit-1_s0_e49",
                "document_name": "handbook.pdf",
                "unit_id": "unit-1",
                "unit_index": 0,
                "locator_kind": "page",
                "location_label": "第 1 页",
                "page_number": 1,
                "slide_number": None,
                "heading_path": [],
                "excerpt": "Employees receive 5 days of annual leave.",
                "document_id": "doc-1",
                "parser_version": "structured-v1",
                "content_type": "paragraph",
                "start_char": 0,
                "end_char": 49,
                "preview_available": False,
            }
        ]

    def test_chat_empty_question_returns_422(self, client):
        response = client.post("/api/chat", json={"question": ""})
        assert response.status_code == 422

    def test_chat_returns_trace_without_changing_answer_or_citations(
        self, client, monkeypatch
    ):
        from backend.models.schemas import (
            RetrievalResponse,
            RetrievalTrace,
            RetrievalTraceCandidate,
            RetrievalTraceChunk,
            RetrievalTraceStage,
        )
        from backend.routers import chat as chat_router

        retrieval = [
            {
                "chunk_id": "c1",
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
                    "parser_version": "structured-v1",
                    "start_char": 0,
                    "end_char": 49,
                },
                "distance": 0.2,
            }
        ]
        trace = RetrievalTrace(
            retrieval_mode="dense",
            reranker_enabled=False,
            chunks={
                "c1": RetrievalTraceChunk(
                    chunk_id="c1",
                    document_name="handbook.pdf",
                    page_number=1,
                    content=retrieval[0]["content"],
                )
            },
            stages=[
                RetrievalTraceStage(
                    name="dense",
                    candidates=[
                        RetrievalTraceCandidate(
                            chunk_id="c1",
                            rank=1,
                            distance=0.2,
                            selected=True,
                        )
                    ],
                )
            ],
            selected_chunk_ids=["c1"],
        )
        monkeypatch.setattr(
            chat_router,
            "retrieve_with_trace",
            lambda question: RetrievalResponse(
                results=retrieval,
                trace=trace,
            ),
            raising=False,
        )
        monkeypatch.setattr(
            "backend.services.retrieval_trace.count_tokens",
            lambda context: 8,
        )

        response = client.post("/api/chat", json={"question": "annual leave?"})

        assert response.status_code == 200
        payload = response.json()
        assert payload["trace"]["chunks"]["c1"]["content"] == retrieval[0][
            "content"
        ]
        assert payload["trace"]["context"]["token_count"] == 8
        assert payload["citations"][0]["chunk_id"] == "c1"

    def test_chat_trace_is_null_when_retriever_does_not_capture_it(self, client):
        payload = client.post(
            "/api/chat", json={"question": "annual leave?"}
        ).json()

        assert payload["trace"] is None


class TestPersistentConversationAPI:
    def test_two_turn_conversation_persists_rewrites_and_replays(
        self,
        client,
        monkeypatch,
    ):
        from backend.routers import chat as chat_router

        monkeypatch.setattr(
            chat_router,
            "ask_with_history",
            lambda question, standalone, context, history: (
                f"回答 {standalone}【来源1】"
            ),
        )
        rewritten = "员工手册中的年假政策还有哪些限制？"
        monkeypatch.setattr(
            chat_router,
            "rewrite_followup",
            lambda question, history: rewritten,
        )

        conversation = client.post("/api/conversations", json={}).json()
        conversation_id = conversation["conversation_id"]
        first_payload = {
            "question": "员工有多少天年假？",
            "conversation_id": conversation_id,
            "request_id": "request-1",
            "expected_last_message_id": None,
        }
        first = client.post("/api/chat", json=first_payload)

        assert first.status_code == 200, first.text
        first_data = first.json()
        assert first_data["standalone_question"] == first_payload["question"]
        assert first_data["conversation_id"] == conversation_id

        second_payload = {
            "question": "还有哪些限制？",
            "conversation_id": conversation_id,
            "request_id": "request-2",
            "expected_last_message_id": first_data["assistant_message_id"],
        }
        second = client.post("/api/chat", json=second_payload)

        assert second.status_code == 200, second.text
        second_data = second.json()
        assert second_data["standalone_question"] == rewritten

        detail = client.get(f"/api/conversations/{conversation_id}").json()
        assert [message["role"] for message in detail["messages"]] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]
        assert detail["messages"][2]["content"] == "还有哪些限制？"
        assert detail["messages"][2]["standalone_question"] == rewritten
        assert detail["messages"][3]["citations"]

        replay = client.post("/api/chat", json=second_payload)
        assert replay.status_code == 200
        assert replay.json()["assistant_message_id"] == second_data[
            "assistant_message_id"
        ]
        assert replay.json()["trace"] is None

    def test_stale_expected_last_message_is_rejected(self, client):
        conversation_id = client.post(
            "/api/conversations",
            json={"title": "测试"},
        ).json()["conversation_id"]

        response = client.post(
            "/api/chat",
            json={
                "question": "问题",
                "conversation_id": conversation_id,
                "request_id": "request-conflict",
                "expected_last_message_id": "stale",
            },
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "conversation_conflict"

    def test_conversation_crud_and_cascade_delete(self, client):
        created = client.post(
            "/api/conversations",
            json={"title": "原标题"},
        ).json()
        conversation_id = created["conversation_id"]

        renamed = client.patch(
            f"/api/conversations/{conversation_id}",
            json={"title": "新标题"},
        )
        assert renamed.json()["title"] == "新标题"
        assert client.get("/api/conversations").json()[0]["conversation_id"] == (
            conversation_id
        )

        deleted = client.delete(f"/api/conversations/{conversation_id}")
        assert deleted.status_code == 200
        assert client.get(f"/api/conversations/{conversation_id}").status_code == 404


class TestStructuredCitationPreviewAPI:
    def test_preview_route_returns_only_structured_payload(
        self,
        client,
        monkeypatch,
    ):
        from backend.config import settings
        from backend.routers import upload as upload_router
        from backend.services import structured_citation_preview

        captured_chunks = []

        def capture_chunks(chunks):
            captured_chunks.extend(chunks)
            return len(chunks)

        monkeypatch.setattr(upload_router, "add_chunks", capture_chunks)
        uploaded = _upload_path(
            client,
            Path("eval/corpus/会议纪要（测试文档）.md"),
        ).json()
        first_chunk = captured_chunks[0]
        raw_chunk = {
            "chunk_id": first_chunk.metadata.chunk_id,
            "content": first_chunk.content,
            "metadata": first_chunk.metadata.model_dump(),
        }
        monkeypatch.setattr(settings, "CITATION_PREVIEW_ENABLED", True)
        monkeypatch.setattr(
            structured_citation_preview,
            "get_chunk_by_id",
            lambda chunk_id: raw_chunk,
        )

        response = client.get(
            f"/api/citations/{uploaded['document_id']}/chunks/"
            f"{first_chunk.metadata.chunk_id}/preview"
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["parser_version"] == "structured-v1"
        assert payload["location"]["kind"] == "section"
        assert payload["canonical_text"]
        assert payload["blocks"]
        assert "parsed_page_text" not in payload
        assert "page_number" not in payload


class TestUploadStructuredUnits:
    """structured-v1 来源单元在上传链路中的集成行为。"""

    def test_successful_upload_persists_units(self, client):
        from backend.config import settings
        from backend.services.parsers.constants import PARSER_VERSION
        from backend.services.unit_store import list_document_units

        uploaded = _upload_fixture(client).json()
        assert uploaded["status"] == "success"
        document_id = uploaded["document_id"]

        units = list_document_units(
            document_id,
            PARSER_VERSION,
            db_path=settings.METADATA_DB_PATH,
        )
        assert units
        assert all(unit.document_id == document_id for unit in units)
        assert all(unit.canonical_text for unit in units)

    def test_failed_ingest_cleans_units(self, client, monkeypatch):
        from backend.config import settings
        from backend.routers import upload as upload_router
        from backend.services.parsers.constants import PARSER_VERSION
        from backend.services.unit_store import list_document_units

        monkeypatch.setattr(
            upload_router,
            "add_chunks",
            lambda chunks: (_ for _ in ()).throw(RuntimeError("向量写入失败")),
        )

        response = _upload_fixture(client)
        assert response.status_code == 500
        from backend.services.document_store import list_documents
        records = list_documents(status=None)
        assert len(records) == 1
        assert list_document_units(
            records[0]["document_id"],
            PARSER_VERSION,
            db_path=settings.METADATA_DB_PATH,
        ) == []

    def test_delete_by_id_cleans_units(self, client):
        from backend.config import settings
        from backend.services.parsers.constants import PARSER_VERSION
        from backend.services.unit_store import list_document_units

        uploaded = _upload_fixture(client).json()
        document_id = uploaded["document_id"]

        assert list_document_units(
            document_id,
            PARSER_VERSION,
            db_path=settings.METADATA_DB_PATH,
        )

        response = client.delete(f"/api/documents/id/{document_id}")
        assert response.status_code == 200

        assert list_document_units(
            document_id,
            PARSER_VERSION,
            db_path=settings.METADATA_DB_PATH,
        ) == []
