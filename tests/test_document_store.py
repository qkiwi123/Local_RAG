"""SQLite 文档登记表测试；每个测试使用独立临时数据库。"""

from backend.services.document_store import (
    create_document,
    delete_record,
    get_document,
    list_documents,
    mark_failed,
    mark_ready,
)


def test_document_lifecycle(temp_dir):
    db_path = f"{temp_dir}/metadata.db"

    created = create_document(
        document_id="doc-1",
        original_name="员工手册.pdf",
        stored_name="doc-1.pdf",
        size_bytes=123,
        db_path=db_path,
    )
    assert created["status"] == "processing"
    assert created["chunk_count"] == 0

    mark_ready("doc-1", 7, db_path)
    ready = get_document("doc-1", db_path)
    assert ready["status"] == "ready"
    assert ready["chunk_count"] == 7
    assert list_documents("ready", db_path) == [ready]

    assert delete_record("doc-1", db_path) is True
    assert get_document("doc-1", db_path) is None


def test_failed_document_is_not_listed_as_ready(temp_dir):
    db_path = f"{temp_dir}/metadata.db"
    create_document("doc-2", "坏文件.pdf", "doc-2.pdf", 10, db_path)

    mark_failed("doc-2", "解析失败", db_path)

    failed = get_document("doc-2", db_path)
    assert failed["status"] == "failed"
    assert failed["error"] == "解析失败"
    assert list_documents("ready", db_path) == []
    assert list_documents(None, db_path) == [failed]


def test_same_original_name_can_have_multiple_versions(temp_dir):
    db_path = f"{temp_dir}/metadata.db"
    create_document("doc-a", "制度.docx", "doc-a.docx", 10, db_path)
    create_document("doc-b", "制度.docx", "doc-b.docx", 11, db_path)
    mark_ready("doc-a", 1, db_path)
    mark_ready("doc-b", 2, db_path)

    records = list_documents("ready", db_path)

    assert {record["document_id"] for record in records} == {"doc-a", "doc-b"}
    assert {record["original_name"] for record in records} == {"制度.docx"}
