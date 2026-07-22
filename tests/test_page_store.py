"""P2-4 page_store 测试。"""
import os

import pytest


@pytest.fixture
def tmp_db(temp_dir):
    return str(os.path.join(temp_dir, "metadata.db"))


@pytest.fixture
def sample_pages():
    return [
        {
            "document_id": "doc-a",
            "parser_version": "text-v1-pypdf2",
            "page_number": 1,
            "parsed_page_text": "Page one content.",
            "content_type": "paragraph",
            "created_at": "2026-07-21T00:00:00Z",
        },
        {
            "document_id": "doc-a",
            "parser_version": "text-v1-pypdf2",
            "page_number": 2,
            "parsed_page_text": "Page two content.",
            "content_type": "paragraph",
            "created_at": "2026-07-21T00:00:00Z",
        },
    ]


class TestPageStore:
    def test_replace_and_read(self, tmp_db, sample_pages):
        from backend.services.page_store import (
            replace_document_pages,
            get_document_page,
        )
        replace_document_pages("doc-a", "text-v1-pypdf2", sample_pages, db_path=tmp_db)

        page = get_document_page("doc-a", "text-v1-pypdf2", 1, db_path=tmp_db)
        assert page is not None
        assert page["parsed_page_text"] == "Page one content."
        assert page["content_type"] == "paragraph"

        page2 = get_document_page("doc-a", "text-v1-pypdf2", 2, db_path=tmp_db)
        assert page2 is not None

    def test_missing_page_returns_none(self, tmp_db, sample_pages):
        from backend.services.page_store import (
            replace_document_pages,
            get_document_page,
        )
        replace_document_pages("doc-a", "text-v1-pypdf2", sample_pages, db_path=tmp_db)

        page = get_document_page("doc-a", "text-v1-pypdf2", 99, db_path=tmp_db)
        assert page is None

    def test_replace_overwrites_old_version(self, tmp_db):
        from backend.services.page_store import (
            replace_document_pages,
            get_document_page,
        )
        pages_v1 = [{
            "document_id": "doc-a",
            "parser_version": "text-v1-pypdf2",
            "page_number": 1,
            "parsed_page_text": "Old content.",
            "content_type": "paragraph",
            "created_at": "2026-01-01T00:00:00Z",
        }]
        pages_v2 = [{
            "document_id": "doc-a",
            "parser_version": "text-v1-pypdf2",
            "page_number": 1,
            "parsed_page_text": "New content.",
            "content_type": "paragraph",
            "created_at": "2026-07-21T00:00:00Z",
        }]

        replace_document_pages("doc-a", "text-v1-pypdf2", pages_v1, db_path=tmp_db)
        replace_document_pages("doc-a", "text-v1-pypdf2", pages_v2, db_path=tmp_db)

        page = get_document_page("doc-a", "text-v1-pypdf2", 1, db_path=tmp_db)
        assert page["parsed_page_text"] == "New content."

        # 旧版本只有 1 页，不应残留
        page2 = get_document_page("doc-a", "text-v1-pypdf2", 2, db_path=tmp_db)
        assert page2 is None

    def test_two_parser_versions_coexist(self, tmp_db):
        from backend.services.page_store import (
            replace_document_pages,
            get_document_page,
        )
        v1 = [{
            "document_id": "doc-a",
            "parser_version": "v1",
            "page_number": 1,
            "parsed_page_text": "V1 text.",
            "content_type": "paragraph",
            "created_at": "2026-01-01T00:00:00Z",
        }]
        v2 = [{
            "document_id": "doc-a",
            "parser_version": "v2",
            "page_number": 1,
            "parsed_page_text": "V2 text.",
            "content_type": "paragraph",
            "created_at": "2026-07-21T00:00:00Z",
        }]

        replace_document_pages("doc-a", "v1", v1, db_path=tmp_db)
        replace_document_pages("doc-a", "v2", v2, db_path=tmp_db)

        p1 = get_document_page("doc-a", "v1", 1, db_path=tmp_db)
        p2 = get_document_page("doc-a", "v2", 1, db_path=tmp_db)
        assert p1["parsed_page_text"] == "V1 text."
        assert p2["parsed_page_text"] == "V2 text."

    def test_transaction_rolls_back_on_error(self, tmp_db):
        from backend.services.page_store import (
            replace_document_pages,
            get_document_page,
        )
        # 第一条合法，第二条违反 CHECK 约束
        bad_pages = [
            {
                "document_id": "doc-a",
                "parser_version": "v1",
                "page_number": 1,
                "parsed_page_text": "Good page.",
                "content_type": "paragraph",
                "created_at": "2026-01-01T00:00:00Z",
            },
            {
                "document_id": "doc-a",
                "parser_version": "v1",
                "page_number": 0,  # 非法页码
                "parsed_page_text": "Bad page.",
                "content_type": "paragraph",
                "created_at": "2026-01-01T00:00:00Z",
            },
        ]

        with pytest.raises(Exception):
            replace_document_pages("doc-a", "v1", bad_pages, db_path=tmp_db)

        # 全部回滚，不应留下任何页
        page = get_document_page("doc-a", "v1", 1, db_path=tmp_db)
        assert page is None

    def test_delete_document_pages(self, tmp_db, sample_pages):
        from backend.services.page_store import (
            replace_document_pages,
            delete_document_pages,
            get_document_page,
        )
        # 两个文档
        other = [{
            "document_id": "doc-b",
            "parser_version": "v1",
            "page_number": 1,
            "parsed_page_text": "Other.",
            "content_type": "paragraph",
            "created_at": "2026-01-01T00:00:00Z",
        }]
        replace_document_pages("doc-a", "text-v1-pypdf2", sample_pages, db_path=tmp_db)
        replace_document_pages("doc-b", "v1", other, db_path=tmp_db)

        delete_document_pages("doc-a", db_path=tmp_db)

        assert get_document_page("doc-a", "text-v1-pypdf2", 1, db_path=tmp_db) is None
        assert get_document_page("doc-a", "text-v1-pypdf2", 2, db_path=tmp_db) is None
        # doc-b 不应受影响
        assert get_document_page("doc-b", "v1", 1, db_path=tmp_db) is not None

    def test_multi_page_clear_before_delete(self, tmp_db, sample_pages):
        from backend.services.page_store import (
            replace_document_pages,
            delete_document_pages,
            get_document_page,
        )
        from backend.services.document_store import (
            create_document,
            delete_record,
        )
        create_document(
            "doc-a", "test.pdf", "doc-a.pdf", 1000, db_path=tmp_db,
        )
        replace_document_pages("doc-a", "text-v1-pypdf2", sample_pages, db_path=tmp_db)

        # 先清理页快照再删 document 记录（应用层保证）
        delete_document_pages("doc-a", db_path=tmp_db)
        delete_record("doc-a", db_path=tmp_db)

        page = get_document_page("doc-a", "text-v1-pypdf2", 1, db_path=tmp_db)
        assert page is None
