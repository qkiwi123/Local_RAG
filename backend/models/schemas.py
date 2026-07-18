"""
Pydantic 数据模型定义。

本模块定义所有 API 的请求体、响应体以及内部传递的数据结构。
所有模块共享这些类型，保证数据格式一致。
"""

from typing import Optional

from pydantic import BaseModel, Field


# ─── 文本块相关 ──────────────────────────────────────

class ChunkMetadata(BaseModel):
    """文本块的元信息，描述该块来自哪份文档的哪个位置。"""
    document_id: str = Field(default="", description="文档稳定 ID；旧数据可为空")
    document_name: str = Field(..., description="文档文件名")
    page_number: int = Field(..., description="页码（从 1 开始）")
    chunk_index: int = Field(..., description="该块在当前文档中的序号（从 0 开始）")
    content_type: str = Field(
        default="paragraph",
        description="解析内容类型：paragraph 或 table",
    )
    chunk_id: str = Field(default="", description="由来源位置确定的稳定 chunk ID")
    block_index: int = Field(default=0, description="同一逻辑页内的 parser 块序号")
    start_char: int = Field(default=0, description="chunk 在解析后逻辑页文本中的起始偏移")
    end_char: int = Field(default=0, description="chunk 在解析后逻辑页文本中的结束偏移")
    char_count: int = Field(default=0, description="chunk 字符数")
    token_count: int = Field(default=0, description="embedding tokenizer 的实际 token 数")


class DocumentChunk(BaseModel):
    """单个文本块，包含内容及其元信息。"""
    content: str = Field(..., description="文本块内容")
    metadata: ChunkMetadata = Field(..., description="文本块元信息")


# ─── 上传接口 ────────────────────────────────────────

class UploadResponse(BaseModel):
    """POST /api/upload 的返回体。"""
    message: str = Field(..., description="上传结果描述")
    document_id: str = Field(..., description="本次入库生成的稳定文档 ID")
    document_name: str = Field(..., description="文档文件名")
    chunk_count: int = Field(..., description="切分出的文本块数量")
    status: str = Field(default="success", description="处理状态：success / error")


# ─── 问答接口 ────────────────────────────────────────

class ChatRequest(BaseModel):
    """POST /api/chat 的请求体。"""
    question: str = Field(..., min_length=1, description="用户提出的自然语言问题")


class Citation(BaseModel):
    """单条引用来源。"""
    source_id: int = Field(..., ge=1, description="回答中的来源编号，如【来源1】中的 1")
    chunk_id: Optional[str] = Field(default=None, description="引用对应的向量库文本块 ID")
    document_name: str = Field(..., description="引用的文档文件名")
    page_number: int = Field(..., description="引用的页码")
    excerpt: str = Field(..., description="引用的文本片段")


class ChatResponse(BaseModel):
    """POST /api/chat 的返回体。"""
    answer: str = Field(..., description="LLM 生成的回答文本")
    citations: list[Citation] = Field(default_factory=list, description="引用来源列表")
