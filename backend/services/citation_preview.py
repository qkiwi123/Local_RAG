"""引用预览服务——从 Chroma 和 SQLite 按需加载并校验完整解析页。

服务层负责全部一致性逻辑：router 只做开关判断、异常映射和响应头。
开关检查在 router 层完成，服务层不做二次判断。
"""

from typing import Optional

from backend.config import settings
from backend.models.schemas import CitationPreview
from backend.services.document_store import get_document
from backend.services.page_store import get_document_page
from backend.services.vector_store import get_chunk_by_id


class CitationPreviewError(ValueError):
    """引用预览失败的基础异常。"""


class CitationPreviewNotFoundError(CitationPreviewError):
    """引用所需的文档、chunk 或页快照不存在/不可用。"""


class CitationPreviewConflictError(CitationPreviewError):
    """chunk 元数据与请求路径或解析版本不一致。"""


class CitationPreviewValidationError(CitationPreviewError):
    """引用定位范围或页文本大小不符合预览约束。"""


def build_citation_preview(
    document_id: str,
    chunk_id: str,
    *,
    db_path: Optional[str] = None,
    _chunk: Optional[dict] = None,
) -> CitationPreview:
    """从 Chroma 和 SQLite 构建经过强一致性校验的引用预览。

    步骤：
    1. 确认文档为 ready
    2. 按 ID 从 Chroma 精确读取 chunk
    3. 验证 chunk metadata 的 document_id 与路径一致
    4. 从 SQLite 读取对应 parser 版本和页码的规范页文本
    5. 验证页大小未超上限
    6. 验证 page_text[start:end] == chunk.content
    7. 构造 CitationPreview

    _chunk 仅用于测试，绕过 Chroma 依赖。

    Raises:
        CitationPreviewNotFoundError: 文档、chunk 或页快照不可用。
        CitationPreviewConflictError: chunk 与请求或解析版本不一致。
        CitationPreviewValidationError: offset 越界、范围非法或页文本过大。
    """
    document = get_document(document_id, db_path=db_path)
    if not document:
        raise CitationPreviewNotFoundError(f"找不到文档: {document_id}")
    if document.get("status") != "ready":
        raise CitationPreviewNotFoundError(
            f"文档未就绪: document_id={document_id} status={document.get('status')}"
        )

    chunk = _chunk if _chunk is not None else get_chunk_by_id(chunk_id)
    if chunk is None:
        raise CitationPreviewNotFoundError(f"chunk 不存在: {chunk_id}")

    meta = chunk.get("metadata") or {}
    chunk_document_id = meta.get("document_id")
    if chunk_document_id != document_id:
        raise CitationPreviewConflictError(
            f"chunk 所属文档不一致: chunk 属于 {chunk_document_id}, "
            f"请求 {document_id}"
        )

    parser_version = meta.get("parser_version", "")
    page_number = meta.get("page_number", 0)
    start_char = meta.get("start_char")
    end_char = meta.get("end_char")

    if not parser_version or not isinstance(page_number, int) or page_number < 1:
        raise CitationPreviewConflictError(
            "索引与解析版本不一致，请重建索引：缺少 parser 版本或页码"
        )
    if not isinstance(start_char, int) or not isinstance(end_char, int):
        raise CitationPreviewConflictError(
            "索引与解析版本不一致，请重建索引：offset 缺失"
        )
    if start_char < 0 or end_char <= start_char:
        raise CitationPreviewValidationError(
            f"引用 offset 非法: start_char={start_char}, end_char={end_char}"
        )

    page = get_document_page(document_id, parser_version, page_number, db_path=db_path)
    if page is None:
        raise CitationPreviewNotFoundError(
            f"缺少引用页快照: document_id={document_id} "
            f"parser={parser_version} page={page_number}"
        )

    parsed_page_text = page["parsed_page_text"]
    if len(parsed_page_text) > settings.CITATION_PREVIEW_MAX_PAGE_CHARS:
        raise CitationPreviewValidationError(
            f"页文本过大 ({len(parsed_page_text)} chars), "
            f"上限 {settings.CITATION_PREVIEW_MAX_PAGE_CHARS}"
        )

    if end_char > len(parsed_page_text):
        raise CitationPreviewValidationError(
            f"引用 offset 越界: end_char={end_char}, "
            f"页文本长度={len(parsed_page_text)}"
        )

    chunk_content = chunk.get("content", "")
    sliced = parsed_page_text[start_char:end_char]
    if sliced != chunk_content:
        raise CitationPreviewConflictError(
            f"索引与解析版本不一致，请重建索引：切片内容不等于 chunk 内容 "
            f"chunk_id={chunk_id}"
        )

    return CitationPreview(
        document_id=document_id,
        chunk_id=chunk_id,
        document_name=document.get("original_name", meta.get("document_name", "")),
        parser_version=parser_version,
        page_number=page_number,
        content_type=meta.get("content_type", page.get("content_type", "paragraph")),
        parsed_page_text=parsed_page_text,
        highlight_start=start_char,
        highlight_end=end_char,
        chunk_content=chunk_content,
    )