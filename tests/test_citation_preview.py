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
