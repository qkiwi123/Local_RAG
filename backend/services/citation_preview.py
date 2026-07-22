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
        ValueError: 在文档非 ready、数据不一致、切片错位、页过大时抛出。
    """
    document = get_document(document_id, db_path=db_path)
    if not document:
        raise ValueError(f"找不到文档: {document_id}")
    if document.get("status") != "ready":
        raise ValueError(f"文档未就绪: document_id={document_id} status={document.get('status')}")

    chunk = _chunk if _chunk is not None else get_chunk_by_id(chunk_id)
    if chunk is None:
        raise ValueError(f"chunk 不存在: {chunk_id}")

    meta = chunk.get("metadata") or {}
    chunk_document_id = meta.get("document_id")
    if chunk_document_id != document_id:
        raise ValueError(
            f"chunk 所属文档不一致: chunk 属于 {chunk_document_id}, "
            f"请求 {document_id}"
        )

    parser_version = meta.get("parser_version", "")
    page_number = meta.get("page_number", 0)
    start_char = meta.get("start_char")
    end_char = meta.get("end_char")

    if not parser_version or not isinstance(page_number, int) or page_number < 1:
        raise ValueError("索引与解析版本不一致，请重建索引：缺少 parser 版本或页码")
    if not isinstance(start_char, int) or not isinstance(end_char, int):
        raise ValueError("索引与解析版本不一致，请重建索引：offset 缺失")
    if start_char < 0 or end_char <= start_char:
        raise ValueError("索引与解析版本不一致，请重建索引：offset 非法")

    page = get_document_page(document_id, parser_version, page_number, db_path=db_path)
    if page is None:
        raise ValueError(
            f"索引与解析版本不一致，请重建索引：缺少页快照 "
            f"document_id={document_id} parser={parser_version} page={page_number}"
        )

    parsed_page_text = page["parsed_page_text"]
    if len(parsed_page_text) > settings.CITATION_PREVIEW_MAX_PAGE_CHARS:
        raise ValueError(
            f"页文本过大 ({len(parsed_page_text)} chars), "
            f"上限 {settings.CITATION_PREVIEW_MAX_PAGE_CHARS}"
        )

    if end_char > len(parsed_page_text):
        raise ValueError(
            f"索引与解析版本不一致，请重建索引：end_char={end_char} "
            f"超出页文本长度 {len(parsed_page_text)}"
        )

    chunk_content = chunk.get("content", "")
    sliced = parsed_page_text[start_char:end_char]
    if sliced != chunk_content:
        raise ValueError(
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
