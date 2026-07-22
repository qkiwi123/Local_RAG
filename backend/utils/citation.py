"""
引用来源提取 — 从 LLM 回答中的稳定 source ID 映射到真实检索结果。

LLM 只能使用 【来源N】。N 是本次检索结果的 1-based 序号，文档名、
页码和 excerpt 全部由后端从检索结果生成，避免模型自由拼写造成伪引用。
没有引用标记时返回空列表，不再把检索 Top-K 伪装成回答引用。
"""

import re
from typing import List
from backend.models.schemas import Citation


# 匹配稳定来源编号；允许“来源”和数字之间存在少量空白。
_SOURCE_PATTERN = re.compile(r"【来源\s*(\d+)】")


def extract_citations(
    answer: str,
    retrieval_results: List[dict],
    *,
    preview_enabled: bool = False,
) -> List[Citation]:
    """
    从 LLM 回答中提取 【来源N】，按 N 映射回本次检索结果。

    流程：
    1. 正则匹配所有 【来源N】
    2. 验证 N 是否落在本次检索结果范围内
    3. 直接从对应 result 读取 chunk_id / metadata / content
    4. 按来源编号去重后返回结构化 Citation

    Args:
        answer: LLM 生成的完整回答文本
        retrieval_results: retrieve() 返回的检索结果列表
        preview_enabled: 全局预览开关状态，决定 preview_available

    Returns:
        Citation 列表（去重）
    """
    citations = []
    seen_source_ids = set()

    for match in _SOURCE_PATTERN.finditer(answer or ""):
        source_id = int(match.group(1))
        if source_id in seen_source_ids:
            continue
        seen_source_ids.add(source_id)

        result_index = source_id - 1
        if result_index < 0 or result_index >= len(retrieval_results):
            # LLM 生成了不存在的编号：不返回伪造 Citation。
            continue

        result = retrieval_results[result_index]
        meta = result.get("metadata") or {}
        doc_name = meta.get("document_name")
        page_num = meta.get("page_number")
        if not doc_name or not isinstance(page_num, int):
            continue

        document_id = meta.get("document_id")
        parser_version = meta.get("parser_version")
        content_type = meta.get("content_type")
        start_char = meta.get("start_char")
        end_char = meta.get("end_char")
        chunk_id = result.get("chunk_id")

        preview_available = (
            preview_enabled
            and document_id is not None
            and parser_version is not None
            and chunk_id is not None
            and start_char is not None
            and end_char is not None
            and isinstance(start_char, int)
            and isinstance(end_char, int)
            and start_char >= 0
            and end_char > start_char
        )

        citations.append(Citation(
            source_id=source_id,
            chunk_id=chunk_id,
            document_name=doc_name,
            page_number=page_num,
            excerpt=result.get("content", "")[:200],
            document_id=document_id,
            parser_version=parser_version,
            content_type=content_type,
            start_char=start_char,
            end_char=end_char,
            preview_available=preview_available,
        ))

    return citations


def format_answer_with_citations(answer: str, citations: List[Citation]) -> str:
    """
    将 LLM 原始回答格式化为带引用来源汇总的 Markdown 文本。

    输出格式：
        原始回答内容
        ---
        **参考来源：**
        1. 《文档名》第X页 — "文本片段预览..."
        2. ...

    Args:
        answer: LLM 原始回答
        citations: extract_citations 返回的引用列表

    Returns:
        Markdown 格式的完整输出
    """
    if not citations:
        return answer

    lines = [answer, "", "---", "", "**参考来源：**", ""]

    for i, citation in enumerate(citations, 1):
        excerpt_preview = citation.excerpt[:100].replace("\n", " ")
        lines.append(
            f"{i}. 【来源{citation.source_id}】《{citation.document_name}》"
            f"第{citation.page_number}页"
        )
        if excerpt_preview:
            lines.append(f'   > "{excerpt_preview}..."')

    return "\n".join(lines)
