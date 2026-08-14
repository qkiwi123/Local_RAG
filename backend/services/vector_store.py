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

import json
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

# 应用进程内的索引版本。BM25 以此判断 Chroma 内容是否发生过增删。
_index_revision = 0


def _bump_index_revision() -> None:
    global _index_revision
    _index_revision += 1


def get_index_revision() -> int:
    """返回当前进程观察到的 Chroma 内容版本。"""
    return _index_revision


def add_chunks(chunks: List[DocumentChunk]) -> int:
    """
    将文本块批量添加到向量数据库。

    流程：
    1. 提取所有块的文本内容
    2. 批量调用 embedding 模型生成向量
    3. 将向量、原文、metadata 一同存入 Chroma

    ID 使用 {document_id}_p{page}_c{chunk_index}。document_id 由上传链路生成，
    因此同名文件可以安全共存；旧调用未提供 document_id 时回退到 document_name。

    Args:
        chunks: DocumentChunk 列表（来自 chunker.chunk_document）

    Returns:
        成功添加的块数量
    """
    if not chunks:
        return 0

    texts = [chunk.content for chunk in chunks]
    ids = [
        chunk.metadata.chunk_id
        or (
            (
                f"{chunk.metadata.unit_id}_c{chunk.metadata.chunk_index}"
                if chunk.metadata.unit_id
                else (
                    f"{chunk.metadata.document_id or chunk.metadata.document_name}"
                    f"_p{chunk.metadata.page_number}_c{chunk.metadata.chunk_index}"
                )
            )
        )
        for chunk in chunks
    ]
    metadatas = []
    for chunk in chunks:
        metadata = {
            "document_id": chunk.metadata.document_id or chunk.metadata.document_name,
            "document_name": chunk.metadata.document_name,
            "chunk_index": chunk.metadata.chunk_index,
            "content_type": chunk.metadata.content_type,
            "block_index": chunk.metadata.block_index,
            "start_char": chunk.metadata.start_char,
            "end_char": chunk.metadata.end_char,
            "char_count": chunk.metadata.char_count,
            "token_count": chunk.metadata.token_count,
            "parser_version": chunk.metadata.parser_version,
        }
        if chunk.metadata.page_number is not None:
            metadata["page_number"] = chunk.metadata.page_number
        if chunk.metadata.unit_id:
            metadata.update({
                "unit_id": chunk.metadata.unit_id,
                "unit_index": chunk.metadata.unit_index,
                "locator_kind": chunk.metadata.locator_kind,
                "location_label": chunk.metadata.location_label,
                "heading_path": json.dumps(
                    chunk.metadata.heading_path,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            })
        if chunk.metadata.slide_number is not None:
            metadata["slide_number"] = chunk.metadata.slide_number
        metadatas.append(metadata)

    # 批量生成向量
    embeddings = embed_texts(texts)

    _collection.add(
        ids=ids,
        documents=texts,
        embeddings=embeddings,
        metadatas=metadatas,
    )
    _bump_index_revision()

    return len(chunks)


def search_by_embedding(
    query_embedding: List[float],
    top_k: int = 5,
    allowed_document_ids: Optional[List[str]] = None,
) -> List[dict]:
    """使用预计算查询向量执行 Chroma 检索，供 Trace 拆分计时。

    allowed_document_ids:
        None 表示全库检索；非 None 时只检索这些文档。空列表表示筛选范围
        为空，直接返回空结果（与 Chroma 空 $in 语义一致）。
    """
    if top_k <= 0:
        return []
    if allowed_document_ids is not None and not allowed_document_ids:
        return []

    where = None
    if allowed_document_ids:
        where = {"document_id": {"$in": list(allowed_document_ids)}}

    results = _collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]
    ids = results.get("ids", [[]])[0]

    return [
        {
            "chunk_id": chunk_id,
            "content": document,
            "metadata": metadata,
            "distance": distance,
        }
        for chunk_id, document, metadata, distance in zip(
            ids, documents, metadatas, distances
        )
    ]


def search(
    query: str,
    top_k: int = 5,
    allowed_document_ids: Optional[List[str]] = None,
) -> List[dict]:
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
        allowed_document_ids: 可选，仅在这些文档中检索；None 表示全库

    Returns:
        List[dict]，每个元素包含:
            - chunk_id: 文本块在向量库中的稳定 ID
            - content: 文本块内容
            - metadata: 元信息字典（document_name, page_number, chunk_index）
            - distance: 余弦距离（越小越相似）
    """
    if top_k <= 0:
        return []

    return search_by_embedding(
        embed_query(query),
        top_k=top_k,
        allowed_document_ids=allowed_document_ids,
    )


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
        _bump_index_revision()
        return True
    except Exception:
        return False


def delete_document_by_id(document_id: str) -> bool:
    """按稳定 document_id 删除文档的全部文本块。"""
    try:
        _collection.delete(where={"document_id": document_id})
        _bump_index_revision()
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


def get_all_chunks() -> List[dict]:
    """读取 Sparse 索引所需的全部 chunk，不加载 embedding。"""
    results = _collection.get(include=["documents", "metadatas"])
    return [
        {
            "chunk_id": chunk_id,
            "content": content,
            "metadata": metadata or {},
        }
        for chunk_id, content, metadata in zip(
            results.get("ids", []),
            results.get("documents", []),
            results.get("metadatas", []),
        )
    ]


def get_chunk_by_id(chunk_id: str) -> Optional[dict]:
    """按精确 chunk ID 从 Chroma 读取单条 chunk 的 content 和 metadata。

    Returns:
        dict with keys chunk_id, content, metadata；若不存在返回 None。
    """
    result = _collection.get(
        ids=[chunk_id],
        include=["documents", "metadatas"],
    )
    ids = result.get("ids", [])
    if not ids:
        return None
    documents = result.get("documents", [])
    metadatas = result.get("metadatas", [])
    return {
        "chunk_id": ids[0],
        "content": documents[0] if documents else "",
        "metadata": metadatas[0] if metadatas else {},
    }
