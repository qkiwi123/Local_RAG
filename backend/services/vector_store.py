"""
向量数据库操作 — 封装 Chroma 的所有增删查操作。

Chroma 提供：
- 持久化存储（PersistentClient）
- 元数据过滤（metadata filtering）
- 基于 embedding 的相似度检索

重要概念：
- distance: Chroma 返回的距离值，越小表示越相似（默认用余弦距离）
- metadata 过滤语法: where={"key": "value"}，仅支持精确匹配
"""

from typing import List, Optional
import chromadb
from chromadb import PersistentClient
from backend.config import settings
from backend.models.schemas import DocumentChunk
from backend.services.embedding import embed_texts, embed_query


# 模块级单例：整个应用共享同一个 Chroma 客户端和 collection
_client: PersistentClient = chromadb.PersistentClient(
    path=settings.CHROMA_PERSIST_DIR
)

# 获取或创建 collection（如果不存在则自动创建）
_collection = _client.get_or_create_collection(
    name="knowledge_base",
    metadata={"hnsw:space": "cosine"}  # 使用余弦距离
)


def add_chunks(chunks: List[DocumentChunk]) -> int:
    """
    将文本块批量添加到向量数据库。

    流程：
    1. 提取所有块的文本内容
    2. 批量调用 embedding 模型生成向量
    3. 将向量、原文、metadata 一同存入 Chroma

    ID 生成规则从 {doc_name}_{chunk_index} 改进为 {doc_name}_p{page}_c{chunk_index}，避免同一文档不同页的块 ID 冲突
    — 同一文档的不同块有唯一 ID，后续可据此更新或删除。

    Args:
        chunks: DocumentChunk 列表（来自 chunker.chunk_document）

    Returns:
        成功添加的块数量
    """
    if not chunks:
        return 0

    texts = [chunk.content for chunk in chunks]
    ids = [
        f"{chunk.metadata.document_name}_p{chunk.metadata.page_number}_c{chunk.metadata.chunk_index}"
        for chunk in chunks
    ]
    metadatas = [
        {
            "document_name": chunk.metadata.document_name,
            "page_number": chunk.metadata.page_number,
            "chunk_index": chunk.metadata.chunk_index,
        }
        for chunk in chunks
    ]

    # 批量生成向量
    embeddings = embed_texts(texts)

    _collection.add(
        ids=ids,
        documents=texts,
        embeddings=embeddings,
        metadatas=metadatas,
    )

    return len(chunks)


def search(query: str, top_k: int = 5) -> List[dict]:
    """
    语义检索：在向量库中搜索与查询最相似的文本块。

    流程：
    1. 将用户问题转为向量（与入库时使用同一个 embedding 模型）
    2. 在 Chroma 中检索 top_k 个最相似的结果
    3. 整理为统一格式返回

    Chroma 返回的 distance 是余弦距离（越小越相似）。

    Args:
        query: 用户搜索查询文本
        top_k: 返回数量上限

    Returns:
        List[dict]，每个元素包含:
            - content: 文本块内容
            - metadata: 元信息字典（document_name, page_number, chunk_index）
            - distance: 余弦距离（越小越相似）
    """
    if top_k <= 0:
        return []

    query_embedding = embed_query(query)

    #向量查询检索，默认按距离从远到近返回top_k个结果
    results = _collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )

    # Chroma 返回的是嵌套列表，取第一组（因为只查询了一个问题）
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    formatted = []
    for doc, meta, dist in zip(documents, metadatas, distances):
        formatted.append({
            "content": doc,
            "metadata": meta,
            "distance": dist,
        })

    return formatted


def delete_document(document_name: str) -> bool:
    """
    按文档名删除所有关联的文本块。

    使用 Chroma 的 metadata 过滤功能，
    一次性删除某个文档的所有块。

    Args:
        document_name: 要删除的文档文件名

    Returns:
        是否删除成功
    """
    try:
        _collection.delete(where={"document_name": document_name})
        return True
    except Exception:
        return False


def get_document_list() -> List[str]:
    """
    获取所有已入库的不重复文档名列表。

    通过查询 Chroma 的所有 metadata 并去重获得，
    供前端展示"已上传文档列表"。

    Returns:
        文档名列表（去重排序）
    """
    # 先获取 collection 中的总数量
    count = _collection.count()
    if count == 0:
        return []

    # 查询所有 metadata（不返回向量，只返回 metadata）
    results = _collection.get(include=["metadatas"])#用get请求matadats数据传到results(执行完后results中获得数据)

    metadatas = results.get("metadatas", [])#在results中get请求matadatas的具体数据值传入matadatas
    document_names = set()#创建去重容器
    for meta in metadatas:
        if meta and "document_name" in meta:#双重安全检查：检测meta是否为空和mata是否有document_name
            document_names.add(meta["document_name"])

    return sorted(document_names)
