"""
语义检索器 — 将用户问题转为向量，在 Chroma 中检索最相关的文档片段。

与直接调用 vector_store.search() 相比，本模块增加了：
1. 相似度阈值过滤：过滤掉 distance 过大的结果，避免基于不相关内容"编"答案
2. 同文档去重：同一文档最多保留 N 条，避免一个长文档垄断返回结果
3. 上下文格式化：将检索结果拼接为 LLM 可直接使用的文本

不直接把 Chroma 返回结果丢给 LLM 的原因：
- 需要去重、整理格式、控制 Prompt 长度
- 低质量的相关结果会误导 LLM，必须先过滤
"""

from typing import List
from backend.config import settings
from backend.services.vector_store import search as vector_search


def retrieve(question: str, top_k: int = None) -> List[dict]:
    """
    语义检索：将用户问题向量化后在 Chroma 中搜索。

    做了两个后处理：
    1. 过滤掉 distance 过大的结果（阈值从 config 读取）
    2. 同一文档最多保留 3 条结果（避免一个长文档垄断返回结果）

    Args:
        question: 用户自然语言问题
        top_k: 返回数量上限（默认使用 config.RETRIEVAL_TOP_K）

    Returns:
        List[dict]，每个元素包含 content, metadata, distance
    """
    if top_k is None:
        top_k = settings.RETRIEVAL_TOP_K

    # 1. 从向量库检索
    raw_results = vector_search(question, top_k=top_k * 2)#多选择数据做候选

    if not raw_results:
        return []

    # 2. 相似度阈值过滤
    # Chroma 用余弦距离，值在 [0, 2] 之间，越小越相似
    # 阈值设为 1.5：超过此值认为基本不相关
    SIMILARITY_THRESHOLD = 1.5
    filtered = [r for r in raw_results if r["distance"] <= SIMILARITY_THRESHOLD]

    # 3. 同文档去重：每个文档最多保留 3 条
    MAX_PER_DOC = 3
    doc_counts = {}
    deduped = []
    for r in filtered:
        doc_name = r["metadata"].get("document_name", "unknown")
        if doc_counts.get(doc_name, 0) < MAX_PER_DOC:
            deduped.append(r)
            doc_counts[doc_name] = doc_counts.get(doc_name, 0) + 1

    # 取最终 top_k
    return deduped[:top_k]


def format_retrieval_context(results: List[dict]) -> str:
    """
    将多条检索结果拼接成一段结构化的上下文文本，供 LLM 使用。

    格式：
        【来源1】《文档名》第X页
        文本内容...

        【来源2】《文档名》第Y页
        文本内容...

    这种格式便于 LLM 理解和引用的同时，也便于 citation.py 后续解析。

    Args:
        results: retrieve() 返回的结果列表

    Returns:
        格式化后的上下文字符串
    """
    if not results:
        return "（未找到相关文档内容）"

    parts = []
    for i, r in enumerate(results, 1):
        doc_name = r["metadata"].get("document_name", "unknown")
        page = r["metadata"].get("page_number", "?")
        content = r["content"]

        parts.append(f"【来源{i}】《{doc_name}》第{page}页\n{content}")

    return "\n\n".join(parts)
