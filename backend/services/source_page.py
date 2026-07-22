"""规范页组装——将 parser 输出按逻辑页组装为确定性的版本化源文本。

每个逻辑页的唯一规范文本由同一 (document_id, parser_version, page_number)
下的多个 parser block 通过 ``\\n\\n`` 连接而成。chunker 的累计偏移量必须与此规则
一致；此模块提供的校验函数强制执行该不变量。
"""

from typing import List

from pydantic import BaseModel, Field

from backend.models.schemas import DocumentChunk


class ParsedPage(BaseModel):
    """单页的版本化规范文本快照。"""
    document_id: str = Field(..., description="稳定文档 ID")
    document_name: str = Field(..., description="文档文件名")
    parser_version: str = Field(..., description="解析器版本")
    page_number: int = Field(..., ge=1, description="逻辑页码")
    parsed_page_text: str = Field(..., description="完整规范页文本")
    content_type: str = Field(
        default="paragraph",
        description="paragraph / table / mixed",
    )


def assemble_source_pages(blocks: list[dict]) -> list[ParsedPage]:
    """将 parser block 按 (document_id, parser_version, page_number) 分组，
    连接为唯一规范页文本。"""
    if not blocks:
        return []

    # 保持输入顺序，按分组键聚合
    pages: list[ParsedPage] = []
    current_key = None
    current_texts: list[str] = []
    current_content_types: set[str] = set()
    current_doc_name = ""
    current_parser = ""
    current_page = 0
    current_doc_id = ""

    for block in blocks:
        doc_id = block.get("document_id", "")
        parser = block.get("parser_version", "")
        page = block.get("page_number", 1)
        key = (doc_id, parser, page)

        if key != current_key:
            if current_key is not None:
                pages.append(_make_page(
                    current_doc_id, current_doc_name, current_parser,
                    current_page, current_texts, current_content_types,
                ))
            current_key = key
            current_texts = []
            current_content_types = set()
            current_doc_name = block.get("document_name", "unknown")
            current_parser = parser
            current_page = page
            current_doc_id = doc_id

        current_texts.append(block.get("text", ""))
        current_content_types.add(block.get("content_type", "paragraph"))

    if current_key is not None:
        pages.append(_make_page(
            current_doc_id, current_doc_name, current_parser,
            current_page, current_texts, current_content_types,
        ))

    return pages


def _make_page(
    doc_id: str,
    doc_name: str,
    parser_version: str,
    page_number: int,
    texts: list[str],
    content_types: set[str],
) -> ParsedPage:
    """从分组后的 text 列表构造单个 ParsedPage。"""
    parsed_page_text = "\n\n".join(texts)
    if len(content_types) == 1:
        ctype = next(iter(content_types))
    else:
        ctype = "mixed"
    return ParsedPage(
        document_id=doc_id,
        document_name=doc_name,
        parser_version=parser_version,
        page_number=page_number,
        parsed_page_text=parsed_page_text,
        content_type=ctype,
    )


def index_source_pages(pages: list[ParsedPage]) -> dict[tuple, ParsedPage]:
    """按 (document_id, parser_version, page_number) 索引页快照。"""
    idx: dict[tuple, ParsedPage] = {}
    for page in pages:
        key = (page.document_id, page.parser_version, page.page_number)
        idx[key] = page
    return idx


def validate_chunks_against_pages(
    chunks: list[DocumentChunk],
    pages: list[ParsedPage],
) -> None:
    """验证每个 chunk 可以从对应版本页文本中精确切片还原。

    Raises:
        ValueError: 当 page_text[start_char:end_char] != chunk.content 时抛出，
            包含 document/page/chunk ID 但不包含完整文档文本。
    """
    idx = index_source_pages(pages)
    for chunk in chunks:
        meta = chunk.metadata
        key = (meta.document_id, meta.parser_version, meta.page_number)
        page = idx.get(key)
        if page is None:
            raise ValueError(
                f"页快照不存在: document_id={meta.document_id} "
                f"parser_version={meta.parser_version} "
                f"page_number={meta.page_number} "
                f"chunk_id={meta.chunk_id}"
            )
        if page.parser_version != meta.parser_version:
            raise ValueError(
                f"chunk 与页快照的 parser 版本不一致: "
                f"chunk({meta.parser_version}) vs page({page.parser_version}) "
                f"chunk_id={meta.chunk_id}"
            )
        sliced = page.parsed_page_text[
            meta.start_char:meta.end_char
        ]
        if sliced != chunk.content:
            raise ValueError(
                f"chunk 内容与页快照切片不一致: "
                f"document_id={meta.document_id} "
                f"page_number={meta.page_number} "
                f"chunk_id={meta.chunk_id} "
                f"start_char={meta.start_char} end_char={meta.end_char} "
                f"expected length={len(sliced)} actual length={len(chunk.content)}"
            )
