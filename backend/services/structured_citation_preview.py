"""structured-v1 引用预览的强一致性读取服务。"""

from __future__ import annotations

import json
from typing import Optional

from backend.config import settings
from backend.models.schemas import StructuredCitationPreview
from backend.services.document_store import get_document
from backend.services.parsers.constants import PARSER_VERSION
from backend.services.unit_store import get_document_unit, list_document_units
from backend.services.vector_store import get_chunk_by_id


class CitationPreviewError(ValueError):
    pass


class CitationPreviewNotFoundError(CitationPreviewError):
    pass


class CitationPreviewConflictError(CitationPreviewError):
    pass


class CitationPreviewValidationError(CitationPreviewError):
    pass


def _decoded_heading_path(value) -> list[str]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as error:
            raise CitationPreviewConflictError(
                "chunk heading_path 不是合法 JSON"
            ) from error
        if isinstance(decoded, list) and all(
            isinstance(item, str) for item in decoded
        ):
            return decoded
    raise CitationPreviewConflictError("chunk heading_path 格式非法")


def build_structured_citation_preview(
    document_id: str,
    chunk_id: str,
    *,
    db_path: Optional[str] = None,
    _chunk: Optional[dict] = None,
) -> StructuredCitationPreview:
    effective_db_path = db_path or settings.METADATA_DB_PATH
    document = get_document(document_id, db_path=effective_db_path)
    if not document or document.get("status") != "ready":
        raise CitationPreviewNotFoundError(f"文档不存在或未就绪: {document_id}")

    chunk = _chunk if _chunk is not None else get_chunk_by_id(chunk_id)
    if not chunk:
        raise CitationPreviewNotFoundError(f"chunk 不存在: {chunk_id}")
    if chunk.get("chunk_id") != chunk_id:
        raise CitationPreviewConflictError("chunk_id 与请求路径不一致")

    metadata = chunk.get("metadata") or {}
    if metadata.get("document_id") != document_id:
        raise CitationPreviewConflictError("chunk 所属 document_id 不一致")
    if metadata.get("parser_version") != PARSER_VERSION:
        raise CitationPreviewConflictError(
            f"chunk 不是当前 {PARSER_VERSION} 索引，请清空并重建"
        )

    unit_id = metadata.get("unit_id")
    if not isinstance(unit_id, str) or not unit_id:
        raise CitationPreviewConflictError("chunk 缺少 unit_id")
    unit = get_document_unit(
        document_id,
        PARSER_VERSION,
        unit_id,
        db_path=effective_db_path,
    )
    if unit is None:
        raise CitationPreviewNotFoundError(f"来源 unit 不存在: {unit_id}")
    document_units = list_document_units(
        document_id,
        PARSER_VERSION,
        db_path=effective_db_path,
    )
    if not document_units:
        raise CitationPreviewNotFoundError(f"文档没有来源 units: {document_id}")
    document_chars = sum(len(item.canonical_text) for item in document_units)
    if document_chars > settings.CITATION_PREVIEW_MAX_DOCUMENT_CHARS:
        raise CitationPreviewValidationError(
            f"整文档文本过大 ({document_chars} chars)"
        )

    expected_locator_metadata = {
        "unit_index": unit.locator.unit_index,
        "locator_kind": unit.locator.kind,
        "location_label": unit.locator.location_label,
    }
    for key, expected in expected_locator_metadata.items():
        if metadata.get(key) != expected:
            raise CitationPreviewConflictError(f"chunk {key} 与 unit 不一致")
    if metadata.get("page_number") != unit.locator.page_number:
        raise CitationPreviewConflictError("chunk page_number 与 unit 不一致")
    if metadata.get("slide_number") != unit.locator.slide_number:
        raise CitationPreviewConflictError("chunk slide_number 与 unit 不一致")
    if _decoded_heading_path(metadata.get("heading_path")) != unit.locator.heading_path:
        raise CitationPreviewConflictError("chunk heading_path 与 unit 不一致")

    if len(unit.canonical_text) > settings.CITATION_PREVIEW_MAX_PAGE_CHARS:
        raise CitationPreviewValidationError(
            f"来源单元文本过大 ({len(unit.canonical_text)} chars)"
        )

    start_char = metadata.get("start_char")
    end_char = metadata.get("end_char")
    if not isinstance(start_char, int) or not isinstance(end_char, int):
        raise CitationPreviewConflictError("chunk offset 缺失")
    if start_char < 0 or end_char <= start_char:
        raise CitationPreviewValidationError("chunk offset 非法")
    if end_char > len(unit.canonical_text):
        raise CitationPreviewValidationError("chunk offset 越界")

    expected_chunk_id = f"{unit.unit_id}_s{start_char}_e{end_char}"
    if chunk_id != expected_chunk_id:
        raise CitationPreviewConflictError("chunk_id 不符合稳定位置规则")
    chunk_content = chunk.get("content", "")
    if unit.canonical_text[start_char:end_char] != chunk_content:
        raise CitationPreviewConflictError("chunk 内容与 unit 切片不一致")

    return StructuredCitationPreview(
        document_id=document_id,
        chunk_id=chunk_id,
        document_name=document["original_name"],
        parser_version=PARSER_VERSION,
        location=unit.locator,
        target_unit_id=unit.unit_id,
        units=[
            {
                "unit_id": item.unit_id,
                "location": item.locator,
                "canonical_text": item.canonical_text,
                "blocks": item.blocks,
            }
            for item in document_units
        ],
        highlight_start=start_char,
        highlight_end=end_char,
    )
