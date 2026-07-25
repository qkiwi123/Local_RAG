import pytest
from pathlib import Path

from backend.models.document_structure import SourceLocator
from backend.services.chunker import chunk_units
from backend.services.document_store import create_document, mark_ready
from backend.services.metadata_schema import bootstrap_structured_database
from backend.services.parsers.assembler import BlockDraft, assemble_unit_from_drafts
from backend.services.parsers.constants import PARSER_VERSION
from backend.services.structured_citation_preview import (
    build_structured_citation_preview,
)
from backend.services.unit_store import replace_document_units


def _prepared_preview(temp_dir):
    db_path = f"{temp_dir}/metadata.db"
    Path(db_path).unlink(missing_ok=True)
    bootstrap_structured_database(
        db_path,
        created_at="2026-07-26T00:00:00+00:00",
    )
    create_document("doc-1", "课件.pptx", "doc-1.pptx", 123, db_path)
    unit = assemble_unit_from_drafts(
        document_id="doc-1",
        document_name="课件.pptx",
        locator=SourceLocator(
            kind="slide",
            unit_index=2,
            location_label="第 3 张幻灯片",
            slide_number=3,
            heading_path=["检索评测"],
        ),
        drafts=[
            BlockDraft(
                block_type="heading",
                text="检索评测",
                heading_level=1,
            ),
            BlockDraft(
                block_type="paragraph",
                text="MRR 用于衡量排序质量。",
            ),
        ],
    )
    chunk = chunk_units([unit], chunk_size=500, overlap=0)[0]
    replace_document_units(
        "doc-1",
        PARSER_VERSION,
        [unit],
        db_path=db_path,
    )
    mark_ready("doc-1", 1, db_path)
    metadata = chunk.metadata.model_dump()
    import json

    metadata["heading_path"] = json.dumps(
        metadata["heading_path"],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    metadata.pop("page_number")
    raw_chunk = {
        "chunk_id": chunk.metadata.chunk_id,
        "content": chunk.content,
        "metadata": metadata,
    }
    return db_path, unit, chunk, raw_chunk


def test_structured_preview_returns_location_blocks_and_exact_slice(temp_dir):
    db_path, unit, chunk, raw_chunk = _prepared_preview(temp_dir)

    preview = build_structured_citation_preview(
        "doc-1",
        chunk.metadata.chunk_id,
        db_path=db_path,
        _chunk=raw_chunk,
    )

    assert preview.location.kind == "slide"
    assert preview.location.slide_number == 3
    assert preview.canonical_text == unit.canonical_text
    assert preview.blocks == unit.blocks
    assert preview.canonical_text[
        preview.highlight_start:preview.highlight_end
    ] == preview.chunk_content


def test_structured_preview_rejects_locator_or_content_mismatch(temp_dir):
    db_path, _unit, chunk, raw_chunk = _prepared_preview(temp_dir)
    raw_chunk["metadata"]["location_label"] = "伪造位置"

    with pytest.raises(ValueError, match="location_label"):
        build_structured_citation_preview(
            "doc-1",
            chunk.metadata.chunk_id,
            db_path=db_path,
            _chunk=raw_chunk,
        )

    db_path, _unit, chunk, raw_chunk = _prepared_preview(temp_dir)
    raw_chunk["content"] = "被篡改"
    with pytest.raises(ValueError, match="切片"):
        build_structured_citation_preview(
            "doc-1",
            chunk.metadata.chunk_id,
            db_path=db_path,
            _chunk=raw_chunk,
        )
