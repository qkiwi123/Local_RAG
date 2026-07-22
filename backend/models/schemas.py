"""
Pydantic 数据模型定义。

本模块定义所有 API 的请求体、响应体以及内部传递的数据结构。
所有模块共享这些类型，保证数据格式一致。
"""

from typing import Optional

from pydantic import BaseModel, Field, model_validator


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
    parser_version: str = Field(
        default="",
        description="解析器版本，用于区分不同解析产出下的 offset 语义",
    )


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
    document_id: Optional[str] = Field(default=None, description="稳定文档 ID")
    parser_version: Optional[str] = Field(default=None, description="解析器版本")
    content_type: Optional[str] = Field(default=None, description="解析内容类型")
    start_char: Optional[int] = Field(default=None, description="chunk 在页文本中的起始偏移")
    end_char: Optional[int] = Field(default=None, description="chunk 在页文本中的结束偏移")
    preview_available: bool = Field(
        default=False,
        description="服务端预览开启且引用元数据具备预览资格",
    )


class CitationPreview(BaseModel):
    """按需加载的引用原文预览，含完整解析页和精确高亮定位。"""
    document_id: str = Field(..., description="稳定文档 ID")
    chunk_id: str = Field(..., description="向量库文本块 ID")
    document_name: str = Field(..., description="文档文件名")
    parser_version: str = Field(..., description="解析器版本")
    page_number: int = Field(..., ge=1, description="逻辑页码")
    content_type: str = Field(..., description="解析内容类型")
    parsed_page_text: str = Field(..., description="完整规范页文本")
    highlight_start: int = Field(..., ge=0, description="高亮起始偏移")
    highlight_end: int = Field(..., ge=0, description="高亮结束偏移")
    chunk_content: str = Field(..., description="被引用的完整 chunk 原文")

    @model_validator(mode="after")
    def _check_offsets(self) -> "CitationPreview":
        if self.highlight_end <= self.highlight_start:
            raise ValueError("highlight_end 必须大于 highlight_start")
        if self.highlight_start < 0:
            raise ValueError("highlight_start 不能为负数")
        return self


class RetrievalTraceChunk(BaseModel):
    """Trace 中按 chunk_id 去重保存的完整候选原文。"""
    chunk_id: str
    document_name: str
    page_number: int
    content: str


class RetrievalTraceCandidate(BaseModel):
    """某个检索阶段中的候选排名与诊断分数。"""
    chunk_id: str
    rank: int = Field(..., ge=1)
    distance: Optional[float] = None
    dense_rank: Optional[int] = None
    bm25_rank: Optional[int] = None
    bm25_score: Optional[float] = None
    rrf_score: Optional[float] = None
    reranker_score: Optional[float] = None
    eligible: bool = True
    filtered_reason: Optional[str] = None
    selected: bool = False


class RetrievalTraceStage(BaseModel):
    """实际执行的一个检索阶段及其候选快照。"""
    name: str
    candidates: list[RetrievalTraceCandidate] = Field(default_factory=list)


class RetrievalContextStats(BaseModel):
    """最终送入 LLM 的上下文规模。"""
    chunk_count: int = 0
    char_count: int = 0
    token_count: int = 0


class RetrievalTraceTimings(BaseModel):
    """检索和生成链路各阶段耗时，单位为毫秒。"""
    query_embedding: float = 0.0
    dense_retrieval: float = 0.0
    sparse_retrieval: float = 0.0
    rrf: float = 0.0
    rerank: float = 0.0
    context_formatting: float = 0.0
    llm_generation: float = 0.0
    total: float = 0.0


class RetrievalTrace(BaseModel):
    """一次问答实际执行的完整检索链路。"""
    retrieval_mode: str
    reranker_enabled: bool
    chunks: dict[str, RetrievalTraceChunk] = Field(default_factory=dict)
    stages: list[RetrievalTraceStage] = Field(default_factory=list)
    selected_chunk_ids: list[str] = Field(default_factory=list)
    context: RetrievalContextStats = Field(default_factory=RetrievalContextStats)
    timings_ms: RetrievalTraceTimings = Field(default_factory=RetrievalTraceTimings)


class RetrievalResponse(BaseModel):
    """内部检索返回：业务结果与可选 Trace 分离。"""
    results: list[dict] = Field(default_factory=list)
    trace: Optional[RetrievalTrace] = None


class ChatResponse(BaseModel):
    """POST /api/chat 的返回体。"""
    answer: str = Field(..., description="LLM 生成的回答文本")
    citations: list[Citation] = Field(default_factory=list, description="引用来源列表")
    trace: Optional[RetrievalTrace] = Field(
        default=None,
        description="服务端允许时返回的检索调试链路",
    )
