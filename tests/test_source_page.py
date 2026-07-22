"""P2-4 source_page 纯函数测试。"""
import pytest
from backend.services.source_page import (
    assemble_source_pages,
    index_source_pages,
    validate_chunks_against_pages,
    ParsedPage,
)


def _block(text, **kwargs):
    b = {
        "text": text,
        "page_number": 1,
        "document_name": "test.pdf",
        "document_id": "doc-1",
        "content_type": "paragraph",
        "parser_version": "text-v1-pypdf2",
    }
    b.update(kwargs)
    return b


class TestAssembleSourcePages:
    def test_single_block_preserved(self):
        pages = assemble_source_pages([_block("Hello world")])
        assert len(pages) == 1
        assert pages[0].parsed_page_text == "Hello world"
        assert pages[0].content_type == "paragraph"

    def test_multiple_blocks_same_page_joined(self):
        pages = assemble_source_pages([
            _block("Paragraph one"),
            _block("Table markdown", content_type="table"),
            _block("Paragraph two"),
        ])
        assert len(pages) == 1
        assert pages[0].parsed_page_text == "Paragraph one\n\nTable markdown\n\nParagraph two"
        assert pages[0].content_type == "mixed"

    def test_table_only_page(self):
        pages = assemble_source_pages([
            _block("| A | B |", content_type="table"),
        ])
        assert len(pages) == 1
        assert pages[0].content_type == "table"

    def test_different_pages_separated(self):
        pages = assemble_source_pages([
            _block("Page 1 text", page_number=1),
            _block("Page 2 text", page_number=2),
        ])
        assert len(pages) == 2
        assert pages[0].parsed_page_text == "Page 1 text"
        assert pages[1].parsed_page_text == "Page 2 text"
        assert pages[0].page_number == 1
        assert pages[1].page_number == 2

    def test_different_documents_separated(self):
        pages = assemble_source_pages([
            _block("Doc A text", document_id="doc-a"),
            _block("Doc B text", document_id="doc-b"),
        ])
        assert len(pages) == 2
        assert pages[0].document_id == "doc-a"
        assert pages[1].document_id == "doc-b"

    def test_different_parser_versions_separated(self):
        """不同 parser 版本的 block 不能合并到同一页。"""
        pages = assemble_source_pages([
            _block("V1 text", parser_version="text-v1-pypdf2"),
            _block("V2 text", parser_version="text-v2"),
        ])
        assert len(pages) == 2

    def test_fields_propagated(self):
        pages = assemble_source_pages([
            _block("Some text", document_name="hello.pdf", parser_version="v1"),
        ])
        assert pages[0].document_name == "hello.pdf"
        assert pages[0].parser_version == "v1"
        assert pages[0].document_id == "doc-1"


class TestIndexSourcePages:
    def test_lookup_by_doc_version_page(self):
        p1 = ParsedPage(
            document_id="doc-1", document_name="a.pdf", parser_version="v1",
            page_number=1, parsed_page_text="AAA", content_type="paragraph",
        )
        p2 = ParsedPage(
            document_id="doc-1", document_name="a.pdf", parser_version="v1",
            page_number=2, parsed_page_text="BBB", content_type="paragraph",
        )
        idx = index_source_pages([p1, p2])
        assert len(idx) == 2
        assert idx[("doc-1", "v1", 1)] is p1
        assert idx[("doc-1", "v1", 2)] is p2


class TestValidateChunksAgainstPages:
    def test_valid_chunk_passes(self):
        from backend.models.schemas import DocumentChunk, ChunkMetadata
        pages = assemble_source_pages([
            _block("ABCDEFGHIJ"),
        ])
        chunk = DocumentChunk(
            content="CDEF",
            metadata=ChunkMetadata(
                document_name="test.pdf",
                page_number=1,
                chunk_index=0,
                document_id="doc-1",
                parser_version="text-v1-pypdf2",
                start_char=2,
                end_char=6,
            ),
        )
        validate_chunks_against_pages([chunk], pages)

    def test_mismatched_chunk_raises(self):
        from backend.models.schemas import DocumentChunk, ChunkMetadata
        pages = assemble_source_pages([
            _block("ABCDEFGHIJ"),
        ])
        chunk = DocumentChunk(
            content="WRONG",
            metadata=ChunkMetadata(
                document_name="test.pdf",
                page_number=1,
                chunk_index=0,
                document_id="doc-1",
                parser_version="text-v1-pypdf2",
                start_char=2,
                end_char=6,
            ),
        )
        with pytest.raises(ValueError, match="切片不一致"):
            validate_chunks_against_pages([chunk], pages)

    def test_missing_page_raises(self):
        from backend.models.schemas import DocumentChunk, ChunkMetadata
        pages: list = []
        chunk = DocumentChunk(
            content="X",
            metadata=ChunkMetadata(
                document_name="test.pdf",
                page_number=1,
                chunk_index=0,
                document_id="doc-1",
                parser_version="text-v1-pypdf2",
                start_char=0,
                end_char=1,
            ),
        )
        with pytest.raises(ValueError, match="页快照不存在"):
            validate_chunks_against_pages([chunk], pages)

    def test_parser_version_mismatch_raises(self):
        from backend.models.schemas import DocumentChunk, ChunkMetadata
        pages = assemble_source_pages([
            _block("ABCDEFGHIJ", parser_version="text-v1-pypdf2"),
        ])
        chunk = DocumentChunk(
            content="CDEF",
            metadata=ChunkMetadata(
                document_name="test.pdf",
                page_number=1,
                chunk_index=0,
                document_id="doc-1",
                parser_version="text-v2-other",
                start_char=2,
                end_char=6,
            ),
        )
        with pytest.raises(ValueError, match="页快照不存在"):
            validate_chunks_against_pages([chunk], pages)

    def test_document_mismatch_raises(self):
        from backend.models.schemas import DocumentChunk, ChunkMetadata
        pages = assemble_source_pages([
            _block("ABCDEFGHIJ", document_id="doc-1"),
        ])
        chunk = DocumentChunk(
            content="CDEF",
            metadata=ChunkMetadata(
                document_name="test.pdf",
                page_number=1,
                chunk_index=0,
                document_id="doc-2",
                parser_version="text-v1-pypdf2",
                start_char=2,
                end_char=6,
            ),
        )
        with pytest.raises(ValueError, match="页快照不存在"):
            validate_chunks_against_pages([chunk], pages)
