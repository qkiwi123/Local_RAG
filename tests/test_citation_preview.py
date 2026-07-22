"""P2-4 引用预览：配置与数据契约测试。"""
import pytest
from backend.config import _env_bool


class TestCitationPreviewConfig:
    def test_citation_preview_is_disabled_in_tests(self):
        from backend.config import settings
        assert settings.CITATION_PREVIEW_ENABLED is False

    def test_preview_page_limit_is_positive(self):
        from backend.config import settings
        assert settings.CITATION_PREVIEW_MAX_PAGE_CHARS > 0

    def test_env_bool_rejects_invalid_preview_value(self, monkeypatch):
        monkeypatch.setenv("CITATION_PREVIEW_ENABLED", "sometimes")
        with pytest.raises(ValueError, match="CITATION_PREVIEW_ENABLED"):
            _env_bool("CITATION_PREVIEW_ENABLED")


class TestCitationPreviewModels:
    def test_legacy_citation_has_default_preview_available_false(self):
        from backend.models.schemas import Citation
        c = Citation(
            source_id=1,
            document_name="test.pdf",
            page_number=1,
            excerpt="...",
        )
        assert c.preview_available is False
        assert c.document_id is None
        assert c.parser_version is None
        assert c.content_type is None
        assert c.start_char is None
        assert c.end_char is None

    def test_chunk_metadata_parser_version_defaults_empty(self):
        from backend.models.schemas import ChunkMetadata
        m = ChunkMetadata(
            document_name="test.pdf",
            page_number=1,
            chunk_index=0,
        )
        assert m.parser_version == ""

    def test_citation_preview_full_roundtrip(self):
        from backend.models.schemas import CitationPreview
        data = {
            "document_id": "abc123",
            "chunk_id": "abc123_p1_b0_s10_e80",
            "document_name": "handbook.pdf",
            "parser_version": "text-v1-pypdf2",
            "page_number": 1,
            "content_type": "paragraph",
            "parsed_page_text": "完整规范页文本内容。",
            "highlight_start": 10,
            "highlight_end": 80,
            "chunk_content": "被引用的完整 chunk",
        }
        preview = CitationPreview(**data)
        assert preview.document_id == "abc123"
        assert preview.highlight_start == 10
        assert preview.highlight_end == 80

    def test_citation_preview_rejects_invalid_page_number(self):
        from backend.models.schemas import CitationPreview
        with pytest.raises(Exception):
            CitationPreview(
                document_id="doc",
                chunk_id="chunk",
                document_name="a.pdf",
                parser_version="v1",
                page_number=0,
                content_type="paragraph",
                parsed_page_text="text",
                highlight_start=0,
                highlight_end=10,
                chunk_content="chunk",
            )

    def test_citation_preview_rejects_invalid_offsets(self):
        from backend.models.schemas import CitationPreview
        with pytest.raises(Exception):
            CitationPreview(
                document_id="doc",
                chunk_id="chunk",
                document_name="a.pdf",
                parser_version="v1",
                page_number=1,
                content_type="paragraph",
                parsed_page_text="text",
                highlight_start=80,
                highlight_end=10,
                chunk_content="chunk",
            )

    def test_citation_preview_rejects_negative_start_char(self):
        from backend.models.schemas import CitationPreview
        with pytest.raises(Exception):
            CitationPreview(
                document_id="doc",
                chunk_id="chunk",
                document_name="a.pdf",
                parser_version="v1",
                page_number=1,
                content_type="paragraph",
                parsed_page_text="text",
                highlight_start=-1,
                highlight_end=10,
                chunk_content="chunk",
            )


class TestCitationPreviewService:
    """P2-4 Task 7：引用预览服务层的强一致性校验。"""

    def test_build_preview_success(self, temp_dir):
        """ready 文档 + 精确 chunk + 同版本页 = 成功返回预览。"""
        import os
        from backend.services.page_store import replace_document_pages
        from backend.services.document_store import create_document, mark_ready

        db_path = os.path.join(temp_dir, "metadata.db")
        create_document("doc-1", "test.pdf", "test.pdf", 1000, db_path=db_path)
        mark_ready("doc-1", 1, db_path=db_path)
        replace_document_pages("doc-1", "text-v1-pypdf2", [
            {
                "document_id": "doc-1",
                "parser_version": "text-v1-pypdf2",
                "page_number": 1,
                "parsed_page_text": "ABCDEFGHIJKLMNOP",
                "content_type": "paragraph",
                "created_at": "2026-01-01T00:00:00Z",
            },
        ], db_path=db_path)

        from backend.services.citation_preview import build_citation_preview

        result = build_citation_preview(
            document_id="doc-1",
            chunk_id="doc-1_p1_b0_s4_e10",
            db_path=db_path,
            # 绕过 Chroma 读取：直接 mock get_chunk_by_id
            _chunk={
                "chunk_id": "doc-1_p1_b0_s4_e10",
                "content": "EFGHIJ",
                "metadata": {
                    "document_id": "doc-1",
                    "parser_version": "text-v1-pypdf2",
                    "page_number": 1,
                    "content_type": "paragraph",
                    "start_char": 4,
                    "end_char": 10,
                    "document_name": "test.pdf",
                },
            },
        )
        assert result.document_id == "doc-1"
        assert result.highlight_start == 4
        assert result.highlight_end == 10
        assert result.chunk_content == "EFGHIJ"
        assert "ABCDEFGHIJKLMNOP" == result.parsed_page_text

    def test_build_preview_document_not_ready(self, temp_dir):
        """processing/failed 文档不可预览。"""
        import os
        from backend.services.document_store import create_document
        from backend.services.citation_preview import build_citation_preview

        db_path = os.path.join(temp_dir, "metadata.db")
        create_document("doc-1", "test.pdf", "test.pdf", 1000, db_path=db_path)

        with pytest.raises(ValueError, match="not ready|未就绪|找不到文档"):
            build_citation_preview(
                document_id="doc-1",
                chunk_id="chunk-1",
                db_path=db_path,
                _chunk={"chunk_id": "chunk-1", "content": "", "metadata": {}},
            )

    def test_build_preview_document_id_mismatch(self, temp_dir):
        """path 中的 document_id 与 chunk metadata 不一致。"""
        import os
        from backend.services.document_store import create_document, mark_ready

        db_path = os.path.join(temp_dir, "metadata.db")
        create_document("doc-1", "test.pdf", "test.pdf", 1000, db_path=db_path)
        mark_ready("doc-1", 1, db_path=db_path)

        from backend.services.citation_preview import build_citation_preview

        with pytest.raises(ValueError, match="不一致|不属于"):
            build_citation_preview(
                document_id="doc-1",
                chunk_id="chunk-1",
                db_path=db_path,
                _chunk={
                    "chunk_id": "chunk-1",
                    "content": "",
                    "metadata": {"document_id": "doc-2"},
                },
            )

    def test_build_preview_slice_mismatch_raises(self, temp_dir):
        """切片内容 != chunk 内容时拒绝。"""
        import os
        from backend.services.page_store import replace_document_pages
        from backend.services.document_store import create_document, mark_ready

        db_path = os.path.join(temp_dir, "metadata.db")
        create_document("doc-1", "test.pdf", "test.pdf", 1000, db_path=db_path)
        mark_ready("doc-1", 1, db_path=db_path)
        replace_document_pages("doc-1", "text-v1-pypdf2", [
            {
                "document_id": "doc-1",
                "parser_version": "text-v1-pypdf2",
                "page_number": 1,
                "parsed_page_text": "ABCDEFGHIJ",
                "content_type": "paragraph",
                "created_at": "2026-01-01T00:00:00Z",
            },
        ], db_path=db_path)

        from backend.services.citation_preview import build_citation_preview

        with pytest.raises(ValueError, match="不一致|不等于|不匹配"):
            build_citation_preview(
                document_id="doc-1",
                chunk_id="chunk-1",
                db_path=db_path,
                _chunk={
                    "chunk_id": "chunk-1",
                    "content": "WRONG",
                    "metadata": {
                        "document_id": "doc-1",
                        "parser_version": "text-v1-pypdf2",
                        "page_number": 1,
                        "start_char": 0,
                        "end_char": 5,
                        "document_name": "test.pdf",
                    },
                },
            )

    def test_build_preview_page_too_large_rejected(self, temp_dir, monkeypatch):
        """页超过 CITATION_PREVIEW_MAX_PAGE_CHARS 时拒绝。"""
        import os
        from backend.services.page_store import replace_document_pages
        from backend.services.document_store import create_document, mark_ready
        from backend.config import settings

        monkeypatch.setattr(settings, "CITATION_PREVIEW_MAX_PAGE_CHARS", 50)

        db_path = os.path.join(temp_dir, "metadata.db")
        create_document("doc-1", "test.pdf", "test.pdf", 1000, db_path=db_path)
        mark_ready("doc-1", 1, db_path=db_path)
        replace_document_pages("doc-1", "text-v1-pypdf2", [
            {
                "document_id": "doc-1",
                "parser_version": "text-v1-pypdf2",
                "page_number": 1,
                "parsed_page_text": "x" * 100,
                "content_type": "paragraph",
                "created_at": "2026-01-01T00:00:00Z",
            },
        ], db_path=db_path)

        from backend.services.citation_preview import build_citation_preview

        with pytest.raises(ValueError, match="过大|超过|上限"):
            build_citation_preview(
                document_id="doc-1",
                chunk_id="chunk-1",
                db_path=db_path,
                _chunk={
                    "chunk_id": "chunk-1",
                    "content": "x" * 5,
                    "metadata": {
                        "document_id": "doc-1",
                        "parser_version": "text-v1-pypdf2",
                        "page_number": 1,
                        "start_char": 0,
                        "end_char": 5,
                        "document_name": "test.pdf",
                    },
                },
            )
