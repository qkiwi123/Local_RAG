"""
文本切分器 — 将解析后的长文本切分为固定大小的文本块。

设计原则：
1. 对每页分别切分，避免跨页导致页码标注混乱
2. 块间有重叠（overlap），避免关键信息被截断在切分边界处
3. chunk_size 和 overlap 从 config 读取，可灵活调整

参数对检索效果的影响：
- chunk_size 太大 → 检索精度下降（返回的文本块包含太多无关内容）
- chunk_size 太小 → 语义不完整（一个完整句子被拆到两个块中）
- overlap 太小 → 关键信息可能正好位于切分边界导致丢失
- overlap 太大 → 冗余增加，检索效率降低
"""

from typing import List
from backend.models.schemas import DocumentChunk, ChunkMetadata


def _split_text(text: str, chunk_size: int, overlap: int) -> List[str]:
    """
    使用滑动窗口将一段文本切分为固定大小的文本块。

    逻辑：从位置 0 开始取 chunk_size 个字符为一块，
    然后滑动 (chunk_size - overlap) 个字符取下一块。
    最后一块如果不足 chunk_size 也保留。

    Args:
        text: 待切分的文本
        chunk_size: 每个块的最大字符数
        overlap: 相邻块之间的重叠字符数

    Returns:
        切分后的文本块列表
    """
    # 空文本或纯空白字符不处理
    if not text or not text.strip():
        return []

    # 文本长度小于 chunk_size 时直接返回一整块
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    step = chunk_size - overlap  # 每次滑动的步长

    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        chunks.append(chunk)
        start += step

    return chunks


def chunk_document(
    pages: List[dict],
    chunk_size: int,
    overlap: int
) -> List[DocumentChunk]:
    """
    将文档按指定大小切分为文本块。

    对每一页单独切分，避免跨页导致页码标注混乱。
    每个文本块绑定文档名、页码和块序号等 metadata。

    Args:
        pages: parser 输出的页面列表，每个元素包含:
               - text: 该页文本
               - page_number: 页码
               - document_name: 文档文件名
        chunk_size: 每个文本块的最大字符数
        overlap: 相邻文本块之间的重叠字符数

    Returns:
        DocumentChunk 列表
    """
    all_chunks: List[DocumentChunk] = []

    for page in pages:
        text = page.get("text", "")
        page_number = page.get("page_number", 1)
        document_name = page.get("document_name", "unknown")
        document_id = page.get("document_id", document_name)

        # 对每页分别切分，避免跨页切分导致页码标注混乱
        page_chunks = _split_text(text, chunk_size, overlap)

        for i, chunk_text in enumerate(page_chunks):#用i和chunk_text遍历page_chunks,用i标记页内的chunk块号
            if not chunk_text.strip():
                continue

            all_chunks.append(DocumentChunk(
                content=chunk_text,
                metadata=ChunkMetadata(
                    document_id=document_id,
                    document_name=document_name,


                    page_number=page_number,
                    chunk_index=i
                )
            ))

    return all_chunks
