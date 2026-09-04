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
    from backend.services.parsers import PARSER_VERSION

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
                "parser_version": PARSER_VERSION,
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
        lambda question, **kwargs: RetrievalResponse(
            results=retrieval, trace=None
        ),
        raising=False,
    )
    monkeypatch.setattr(
        chat_router,
        "ask",
        lambda question, context, runtime=None: (
            "Employees receive 5 days of annual leave.【来源1】"
        ),
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
        from backend.services.parsers import PARSER_VERSION

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
                "parser_version": PARSER_VERSION,
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
        from backend.config import settings as app_settings
        from backend.models.schemas import (
            RetrievalResponse,
            RetrievalTrace,
            RetrievalTraceCandidate,
            RetrievalTraceChunk,
            RetrievalTraceStage,
        )
        from backend.routers import chat as chat_router
        from backend.services.parsers import PARSER_VERSION

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
                    "parser_version": PARSER_VERSION,
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
            lambda question, **kwargs: RetrievalResponse(
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
        assert payload["trace"]["settings_revision"] == 0
        assert payload["trace"]["llm"]["called"] is True
        assert payload["trace"]["llm"]["provider"] == app_settings.LLM_PROVIDER
        assert "api_key" not in response.text.lower()
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
            lambda question, standalone, context, history, runtime=None: (
                f"回答 {standalone}【来源1】"
            ),
        )
        rewritten = "员工手册中的年假政策还有哪些限制？"
        monkeypatch.setattr(
            chat_router,
            "rewrite_followup",
            lambda question, history, runtime=None: rewritten,
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

    def test_persistent_chat_saves_trace_and_restores_on_reload(
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
        from backend.services.parsers import PARSER_VERSION

        trace = RetrievalTrace(
            retrieval_mode="dense",
            reranker_enabled=False,
            chunks={
                "c1": RetrievalTraceChunk(
                    chunk_id="c1",
                    document_name="handbook.pdf",
                    page_number=1,
                    content="Employees receive 5 days of annual leave.",
                )
            },
            stages=[
                RetrievalTraceStage(
                    name="dense",
                    candidates=[
                        RetrievalTraceCandidate(
                            chunk_id="c1", rank=1, distance=0.2, selected=True
                        )
                    ],
                )
            ],
            selected_chunk_ids=["c1"],
        )
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
                    "parser_version": PARSER_VERSION,
                    "start_char": 0,
                    "end_char": 49,
                },
                "distance": 0.2,
            }
        ]
        monkeypatch.setattr(
            chat_router,
            "retrieve_with_trace",
            lambda question, **kwargs: RetrievalResponse(
                results=retrieval, trace=trace
            ),
            raising=False,
        )
        monkeypatch.setattr(
            chat_router,
            "ask_with_history",
            lambda question, standalone, context, history, runtime=None: (
                "回答【来源1】"
            ),
        )

        conversation_id = client.post("/api/conversations", json={}).json()[
            "conversation_id"
        ]
        payload = {
            "question": "annual leave?",
            "conversation_id": conversation_id,
            "request_id": "request-trace-1",
            "expected_last_message_id": None,
        }
        first = client.post("/api/chat", json=payload)
        assert first.status_code == 200, first.text
        assert first.json()["trace"]["retrieval_mode"] == "dense"
        assert first.json()["trace"]["settings_revision"] == 0
        assert first.json()["trace"]["llm"]["called"] is True

        detail = client.get(f"/api/conversations/{conversation_id}").json()
        assistant = detail["messages"][1]
        assert assistant["trace"]["retrieval_mode"] == "dense"
        assert assistant["trace"]["settings_revision"] == 0

        replay = client.post("/api/chat", json=payload)
        assert replay.status_code == 200
        assert replay.json()["trace"]["retrieval_mode"] == "dense"
        assert replay.json()["trace"]["settings_revision"] == 0
        assert replay.json()["trace"]["llm"]["called"] is True

        deleted = client.delete(f"/api/conversations/{conversation_id}")
        assert deleted.status_code == 200
        from backend.services.conversation_store import load_trace
        from backend.config import settings as app_settings

        assert load_trace(
            assistant["message_id"],
            db_path=app_settings.METADATA_DB_PATH,
        ) is None

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
        from backend.services.parsers import PARSER_VERSION

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
        assert payload["parser_version"] == PARSER_VERSION
        assert payload["location"]["kind"] == "section"
        assert payload["target_unit_id"]
        assert payload["units"]
        assert payload["units"][0]["canonical_text"]
        assert payload["units"][0]["blocks"]
        assert "parsed_page_text" not in payload
        assert "page_number" not in payload
        assert "chunk_content" not in payload
        assert "canonical_text" not in payload


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


class TestTagAPI:
    def test_create_tag_auto_creates_parents_and_is_idempotent(self, client):
        first = client.post(
            "/api/tags", json={"tag_path": "课程课件/数据结构"}
        )
        second = client.post(
            "/api/tags", json={"tag_path": "课程课件/数据结构"}
        )

        assert first.status_code == 200, first.text
        assert first.json()["tag_path"] == "课程课件/数据结构"
        assert first.json()["name"] == "数据结构"
        assert second.json()["tag_path"] == "课程课件/数据结构"

        paths = {
            tag["tag_path"] for tag in client.get("/api/tags").json()
        }
        assert {"课程课件", "课程课件/数据结构"} <= paths

    def test_invalid_tag_path_returns_422(self, client):
        for bad in ("", "  ", "课程/", "/课程", "课程//结构"):
            response = client.post("/api/tags", json={"tag_path": bad})
            assert response.status_code == 422, bad

    def test_rename_and_delete_tree(self, client):
        client.post("/api/tags", json={"tag_path": "旧分类/子类"})

        renamed = client.patch(
            "/api/tags/旧分类", json={"new_path": "新分类"}
        )
        assert renamed.status_code == 200, renamed.text
        assert {
            tag["tag_path"] for tag in client.get("/api/tags").json()
        } >= {"新分类", "新分类/子类"}

        deleted = client.delete("/api/tags/新分类")
        assert deleted.status_code == 200
        assert deleted.json()["removed_tag_count"] == 2
        assert client.get("/api/tags").json() == []

    def test_missing_tag_returns_404(self, client):
        assert client.patch(
            "/api/tags/不存在", json={"new_path": "新"}
        ).status_code == 404
        assert client.delete("/api/tags/不存在").status_code == 404

    def test_upload_with_tags_lists_and_counts(self, client):
        uploaded = _upload_path(client, Path("tests/fixtures/sample.txt"))
        assert uploaded.status_code == 200, uploaded.text
        document_id = uploaded.json()["document_id"]

        response = client.post(
            "/api/upload",
            files={
                "file": (
                    "带标签.txt",
                    Path("tests/fixtures/sample.txt").read_bytes(),
                    "application/octet-stream",
                )
            },
            data={"tags": ["课程课件/数据结构", "规章制度"]},
        )
        assert response.status_code == 200, response.text
        tagged_id = response.json()["document_id"]
        assert tagged_id != document_id

        items = {
            item["document_id"]: item
            for item in client.get("/api/documents").json()["document_items"]
        }
        assert items[tagged_id]["tags"] == ["规章制度", "课程课件/数据结构"]
        assert items[document_id]["tags"] == []

        tree = {
            tag["tag_path"]: tag
            for tag in client.get("/api/tags").json()
        }
        assert tree["课程课件"]["document_count"] == 1
        assert tree["课程课件"]["direct_document_count"] == 0
        assert tree["规章制度"]["document_count"] == 1
        assert tree["规章制度"]["direct_document_count"] == 1

    def test_upload_with_invalid_tag_returns_422(self, client):
        response = client.post(
            "/api/upload",
            files={
                "file": (
                    "bad.txt",
                    Path("tests/fixtures/sample.txt").read_bytes(),
                    "application/octet-stream",
                )
            },
            data={"tags": ["坏/标签/"]},
        )
        assert response.status_code == 422

    def test_update_document_tags_replaces_and_validates(self, client):
        uploaded = _upload_fixture(client).json()
        document_id = uploaded["document_id"]

        replaced = client.put(
            f"/api/documents/id/{document_id}/tags",
            json={"tags": ["新分类/子类", "另一分类"]},
        )
        assert replaced.status_code == 200, replaced.text
        assert replaced.json()["tags"] == ["另一分类", "新分类/子类"]

        items = {
            item["document_id"]: item
            for item in client.get("/api/documents").json()["document_items"]
        }
        assert items[document_id]["tags"] == ["另一分类", "新分类/子类"]

        assert client.put(
            f"/api/documents/id/{document_id}/tags",
            json={"tags": ["坏/标签/"]},
        ).status_code == 422
        assert client.put(
            "/api/documents/id/missing-doc/tags",
            json={"tags": ["A"]},
        ).status_code == 404

    def test_delete_document_cascades_tag_links(self, client):
        uploaded = client.post(
            "/api/upload",
            files={
                "file": (
                    "临时.txt",
                    Path("tests/fixtures/sample.txt").read_bytes(),
                    "application/octet-stream",
                )
            },
            data={"tags": ["课程课件"]},
        ).json()
        document_id = uploaded["document_id"]

        assert client.delete(
            f"/api/documents/id/{document_id}"
        ).status_code == 200

        tree = {
            tag["tag_path"]: tag["document_count"]
            for tag in client.get("/api/tags").json()
        }
        assert tree.get("课程课件", 0) == 0

    def test_chat_passes_resolved_document_filter(self, client, monkeypatch):
        from backend.models.schemas import RetrievalResponse
        from backend.routers import chat as chat_router

        uploaded = client.post(
            "/api/upload",
            files={
                "file": (
                    "筛选.txt",
                    Path("tests/fixtures/sample.txt").read_bytes(),
                    "application/octet-stream",
                )
            },
            data={"tags": ["课程课件/数据结构"]},
        ).json()
        document_id = uploaded["document_id"]

        captured = {}
        monkeypatch.setattr(
            chat_router,
            "retrieve_with_trace",
            lambda question, **kwargs: captured.update(kwargs)
            or RetrievalResponse(results=[], trace=None),
            raising=False,
        )

        response = client.post(
            "/api/chat",
            json={"question": "问题", "tags": ["课程课件"]},
        )

        assert response.status_code == 200, response.text
        assert captured["allowed_document_ids"] == [document_id]
        assert captured["tag_filter"] == ["课程课件"]
        assert "当前筛选范围内未找到" in response.json()["answer"]

    def test_chat_without_tags_searches_everything(self, client, monkeypatch):
        from backend.models.schemas import RetrievalResponse
        from backend.routers import chat as chat_router

        captured = {}
        monkeypatch.setattr(
            chat_router,
            "retrieve_with_trace",
            lambda question, **kwargs: captured.update(kwargs)
            or RetrievalResponse(results=[], trace=None),
            raising=False,
        )

        response = client.post(
            "/api/chat", json={"question": "问题"}
        )

        assert response.status_code == 200
        assert captured["allowed_document_ids"] is None
        assert captured["tag_filter"] == []
        assert "未找到与您问题相关的文档内容" in response.json()["answer"]

    def test_chat_with_invalid_tag_returns_422(self, client):
        response = client.post(
            "/api/chat", json={"question": "问题", "tags": ["坏/标签/"]}
        )
        assert response.status_code == 422


class TestDocumentAPI:
    def test_upload_response_includes_parse_warnings(self, client, monkeypatch):
        from backend.models.document_structure import ParseWarning
        from backend.routers import upload as upload_router

        real_parse = upload_router.parse_document_structure

        def parse_with_warning(path, *, document_id, document_name=None):
            parsed = real_parse(
                path, document_id=document_id, document_name=document_name
            )
            warnings = list(parsed.warnings) + [
                ParseWarning(
                    code="ocr_page_failed",
                    message="第 1 页 OCR 失败：模型不可用",
                    locator=parsed.units[0].locator,
                )
            ]
            return parsed.model_copy(update={"warnings": warnings})

        monkeypatch.setattr(upload_router, "parse_document_structure", parse_with_warning)

        response = _upload_fixture(client)

        assert response.status_code == 200, response.text
        warnings = response.json()["warnings"]
        assert warnings[0]["code"] == "ocr_page_failed"
        assert warnings[0]["message"] == "第 1 页 OCR 失败：模型不可用"
        assert warnings[0]["location_label"]

    def test_documents_list_filters_by_tags(self, client):
        def upload_with_tags(name, tags):
            return client.post(
                "/api/upload",
                files={
                    "file": (
                        name,
                        Path("tests/fixtures/sample.txt").read_bytes(),
                        "application/octet-stream",
                    )
                },
                data={"tags": tags},
            )

        course = upload_with_tags("课程.txt", ["课程课件/数据结构"]).json()
        policy = upload_with_tags("制度.txt", ["规章制度"]).json()

        all_items = client.get("/api/documents").json()["document_items"]
        assert len(all_items) >= 2

        filtered = client.get(
            "/api/documents", params={"tags": ["课程课件"]}
        ).json()["document_items"]
        assert [item["document_id"] for item in filtered] == [course["document_id"]]

        union = client.get(
            "/api/documents",
            params={"tags": ["课程课件", "规章制度"]},
        ).json()["document_items"]
        assert {item["document_id"] for item in union} == {
            course["document_id"],
            policy["document_id"],
        }

        invalid = client.get(
            "/api/documents", params={"tags": ["坏/标签/"]}
        )
        assert invalid.status_code == 422

    def test_documents_list_searches_by_name_and_combines_with_tags(self, client):
        def upload_with_tags(name, tags):
            return client.post(
                "/api/upload",
                files={
                    "file": (
                        name,
                        Path("tests/fixtures/sample.txt").read_bytes(),
                        "application/octet-stream",
                    )
                },
                data={"tags": tags},
            )

        course = upload_with_tags("数据结构课程.txt", ["课程课件/数据结构"]).json()
        policy = upload_with_tags("网络安全制度.txt", ["规章制度"]).json()

        by_name = client.get(
            "/api/documents", params={"q": "课程"}
        ).json()["document_items"]
        assert [item["document_id"] for item in by_name] == [course["document_id"]]

        literal_wildcard = client.get(
            "/api/documents", params={"q": "%"}
        ).json()["document_items"]
        assert [item["document_id"] for item in literal_wildcard] == []

        combined = client.get(
            "/api/documents",
            params={"q": "制度", "tags": ["规章制度"]},
        ).json()["document_items"]
        assert [item["document_id"] for item in combined] == [policy["document_id"]]

        assert client.get(
            "/api/documents", params={"q": "x" * 201}
        ).status_code == 422

    def test_document_preview_returns_structured_units(self, client):
        uploaded = _upload_path(
            client,
            Path("eval/corpus/会议纪要（测试文档）.md"),
        ).json()

        response = client.get(
            f"/api/documents/id/{uploaded['document_id']}/preview"
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["document_id"] == uploaded["document_id"]
        assert payload["parser_version"]
        assert payload["unit_count"] == len(payload["units"])
        assert payload["units"]
        unit = payload["units"][0]
        assert unit["location"]
        assert unit["canonical_text"]
        assert unit["blocks"]

    def test_document_preview_missing_or_deleted_returns_404(self, client):
        assert client.get(
            "/api/documents/id/missing-doc/preview"
        ).status_code == 404

        uploaded = _upload_fixture(client).json()
        assert client.delete(
            f"/api/documents/id/{uploaded['document_id']}"
        ).status_code == 200
        assert client.get(
            f"/api/documents/id/{uploaded['document_id']}/preview"
        ).status_code == 404
