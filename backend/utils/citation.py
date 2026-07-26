"""
引用来源提取 — 从 LLM 回答中的稳定 source ID 映射到真实检索结果。

LLM 只能使用 【来源N】。N 是本次检索结果的 1-based 序号，文档名、
页码和 excerpt 全部由后端从检索结果生成，避免模型自由拼写造成伪引用。
没有引用标记时返回空列表，不再把检索 Top-K 伪装成回答引用。
"""

import json
import re
from typing import List

from backend.models.schemas import Citation
from backend.services.parsers.constants import PARSER_VERSION


# 匹配稳定来源编号；允许“来源”和数字之间存在少量空白。
_SOURCE_PATTERN = re.compile(r"【来源\s*(\d+)】")
_LOCATOR_KINDS = {"page", "virtual_page", "slide", "section", "sheet"}


def _heading_path(value) -> list[str] | None:
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return None
        if isinstance(decoded, list) and all(
            isinstance(item, str) for item in decoded
        ):
            return decoded
    return None


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
        document_id = meta.get("document_id")
        unit_id = meta.get("unit_id")
        unit_index = meta.get("unit_index")
        locator_kind = meta.get("locator_kind")
        location_label = meta.get("location_label")
        parser_version = meta.get("parser_version")
        start_char = meta.get("start_char")
        end_char = meta.get("end_char")
        chunk_id = result.get("chunk_id")
        heading_path = _heading_path(meta.get("heading_path"))

        if (
            not isinstance(doc_name, str)
            or not doc_name
            or not isinstance(document_id, str)
            or not document_id
            or not isinstance(unit_id, str)
            or not unit_id
            or not isinstance(unit_index, int)
            or unit_index < 0
            or locator_kind not in _LOCATOR_KINDS
            or not isinstance(location_label, str)
            or not location_label
            or parser_version != PARSER_VERSION
            or not isinstance(chunk_id, str)
            or not chunk_id
            or not isinstance(start_char, int)
            or not isinstance(end_char, int)
            or start_char < 0
            or end_char <= start_char
            or heading_path is None
        ):
            continue

        content_type = meta.get("content_type")
        page_num = meta.get("page_number")
        slide_num = meta.get("slide_number")
        if page_num is not None and (
            not isinstance(page_num, int) or page_num < 1
        ):
            continue
        if slide_num is not None and (
            not isinstance(slide_num, int) or slide_num < 1
        ):
            continue

        citations.append(Citation(
            source_id=source_id,
            chunk_id=chunk_id,
            document_name=doc_name,
            unit_id=unit_id,
            unit_index=unit_index,
            locator_kind=locator_kind,
            location_label=location_label,
            page_number=page_num,
            slide_number=slide_num,
            heading_path=heading_path,
            excerpt=result.get("content", "")[:200],
            document_id=document_id,
            parser_version=parser_version,
            content_type=content_type,
            start_char=start_char,
            end_char=end_char,
            preview_available=preview_enabled,
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
            f"{citation.location_label or '位置未知'}"
        )
        if excerpt_preview:
            lines.append(f'   > "{excerpt_preview}..."')

    return "\n".join(lines)
