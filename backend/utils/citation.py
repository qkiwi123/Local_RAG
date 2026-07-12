"""
引用来源提取 — 从 LLM 的回答中解析出引用标注，匹配回具体的文档名和页码。

LLM 被要求在回答中使用格式 【文档名，第X页】 进行引用。
本模块用正则表达式提取这些标记，并与检索结果关联，生成结构化引用信息。

当 LLM 没有生成引用标记时的回退策略：
在文末附上所有检索到的来源，确保用户知道信息来源。
"""

import re
from typing import List
from backend.models.schemas import Citation


# 匹配 【文档名，第X页】 格式的正则表达式
# 支持中文书名号也可能被 LLM 省略或混合使用
_CITATION_PATTERN = re.compile(
    r"【([^】]+?)[，,](?:第)?(\d+)(?:页)?】"
)


def extract_citations(
    answer: str,
    retrieval_results: List[dict]
) -> List[Citation]:
    """
    从 LLM 回答中提取引用标记，匹配回检索结果中的 metadata。

    流程：
    1. 正则匹配所有 【文档名，第X页】 格式的引用标记
    2. 将匹配到的引用与检索结果做关联（按文档名 + 页码匹配）
    3. 去重后返回结构化的 Citation 列表

    Args:
        answer: LLM 生成的完整回答文本
        retrieval_results: retrieve() 返回的检索结果列表

    Returns:
        Citation 列表（去重）
    """
    citations = []
    seen = set()  # 用于去重

    # 1. 正则解析 LLM 回答中的引用标记
    matches = _CITATION_PATTERN.findall(answer)

    if matches:
        # 2. 有引用标记 → 匹配检索结果中的 metadata
        for doc_name, page_num in matches:
            # 清理文档名（LLM 可能包含或不包含书名号/扩展名）
            doc_name = doc_name.strip().strip("《》")

            # 在检索结果中查找匹配的文本片段
            excerpt = _find_excerpt(retrieval_results, doc_name, int(page_num))

            # 去重：同一文档 + 同一页码只算一条
            key = (doc_name, int(page_num))
            if key not in seen:
                seen.add(key)
                citations.append(Citation(
                    document_name=doc_name,
                    page_number=int(page_num),
                    excerpt=excerpt,
                ))
    else:
        # 3. 回退：LLM 没有生成引用标记 → 在文末附上所有检索来源
        for result in retrieval_results[:3]:  # 最多附 3 条，避免太长
            meta = result.get("metadata", {})
            doc_name = meta.get("document_name", "unknown")
            page_num = meta.get("page_number", 1)
            content = result.get("content", "")

            key = (doc_name, page_num)
            if key not in seen:
                seen.add(key)
                citations.append(Citation(
                    document_name=doc_name,
                    page_number=page_num,
                    excerpt=content[:200],  # 截取前 200 字符作为预览
                ))

    return citations


def _find_excerpt(
    retrieval_results: List[dict],
    doc_name: str,
    page_number: int
) -> str:
    """
    在检索结果中查找匹配的文本片段。

    先精确匹配（文档名 + 页码），找不到则模糊匹配文档名，
    再找不到则返回第一条结果的摘要。

    Args:
        retrieval_results: 检索结果
        doc_name: 引用中的文档名
        page_number: 引用中的页码

    Returns:
        匹配到的文本片段（前 200 字符）
    """
    # 精确匹配
    for result in retrieval_results:
        meta = result.get("metadata", {})
        if (meta.get("document_name") == doc_name
                and meta.get("page_number") == page_number):
            return result.get("content", "")[:200]

    # 模糊匹配：只匹配文档名
    for result in retrieval_results:
        meta = result.get("metadata", {})
        if meta.get("document_name") == doc_name:
            return result.get("content", "")[:200]

    # 完全找不到匹配 → 返回空
    return ""


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
            f"{i}. 《{citation.document_name}》"
            f"第{citation.page_number}页"
        )
        if excerpt_preview:
            lines.append(f'   > "{excerpt_preview}..."')

    return "\n".join(lines)
