"""
嵌入模型 — 将文本（文本块/用户问题）转换为向量。

使用 sentence-transformers 库加载模型，供 Chroma 存储和检索。
模型在模块加载时初始化一次，后续所有调用复用同一个实例。

关于 normalize_embeddings：
  开启后所有向量长度归一化为 1，此时向量点积等价于余弦相似度，
  计算更快且数值更稳定，是向量检索领域的标准做法。

批处理 vs 单条处理：
  embed_texts 支持一次传入多条文本，sentence-transformers 内部
  会利用矩阵运算加速，比逐条调用 embed_query 高效得多。
"""

from functools import lru_cache
from typing import List
from backend.config import settings


# 惰性单例：首次真正编码时才加载，避免导入 API/运行纯单测就占用模型内存。
# all-MiniLM-L6-v2 首次加载时约 380MB 磁盘空间，384 维向量
# local_files_only=True: 仅使用本地缓存，不联网（模型已在首次下载到
#   C:\Users\Acer\.cache\huggingface\hub\models--sentence-transformers--all-MiniLM-L6-v2）
#   若尚未下载，需先联网下载一次：设置 HF_HUB_OFFLINE=0 启动
@lru_cache(maxsize=1)
def _get_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(
        settings.EMBEDDING_MODEL,
        local_files_only=True,
    )


def embed_texts(texts: List[str]) -> List[List[float]]:
    """
    将多个文本批量转换为向量。

    Args:
        texts: 文本字符串列表（批处理，效率更高）

    Returns:
        对应向量列表，每个向量为 384 维 float 列表（模型 all-MiniLM-L6-v2）
    """
    if not texts:
        return []

    # normalize_embeddings=True: 归一化后点积等价于余弦相似度
    embeddings = _get_model().encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=False  # 测试和 API 调用时不需要进度条
    )
    return embeddings.tolist()


def embed_query(query: str) -> List[float]:
    """
    单条文本向量化的便捷封装。

    内部调用 embed_texts，取结果的第一个向量。
    用于将用户问题转换为向量以进行语义检索。

    Args:
        query: 用户问题或查询文本

    Returns:
        384 维 float 列表
    """
    return embed_texts([query])[0]
    #复用embed_texts,确保所有查询向量和文档向量都处于同种embed计算方式，统一对向量归一化和统一输出二维浮点数列表
