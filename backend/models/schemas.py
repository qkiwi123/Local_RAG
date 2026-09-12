"""
Pydantic 数据模型定义。

本模块定义所有 API 的请求体、响应体以及内部传递的数据结构。
所有模块共享这些类型，保证数据格式一致。
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.models.document_structure import LocatedBlock, SourceLocator

# ─── 文本块相关 ──────────────────────────────────────

class ChunkMetadata(BaseModel):
    """文本块的元信息，描述该块来自哪份文档的哪个位置。"""
    document_id: str = Field(default="", description="文档稳定 ID；旧数据可为空")
    document_name: str = Field(..., description="文档文件名")
    unit_id: str = Field(default="", description="结构化来源单元的稳定 ID")
    unit_index: int = Field(default=0, description="来源单元在文档内的序号")
    locator_kind: str = Field(default="", description="page / virtual_page / slide / section")
    location_label: str = Field(default="", description="面向用户的来源位置标签")
    page_number: Optional[int] = Field(
        default=None,
        description="PDF 或逻辑页页码；非页来源为空",
    )
    slide_number: Optional[int] = Field(
        default=None,
        description="PPTX 幻灯片编号；非幻灯片来源为空",
    )
    heading_path: list[str] = Field(default_factory=list, description="来源标题路径")
    chunk_index: int = Field(..., description="该块在当前来源单元中的序号（从 0 开始）")
    content_type: str = Field(
        default="paragraph",
        description="解析内容类型：paragraph 或 table",
    )
    chunk_id: str = Field(default="", description="由来源位置确定的稳定 chunk ID")
    block_index: int = Field(
        default=0,
        description="兼容旧链路的 parser 块序号；结构化 chunk 取首个相交 block",
    )
    start_char: int = Field(default=0, description="chunk 在来源单元规范文本中的起始偏移")
    end_char: int = Field(default=0, description="chunk 在来源单元规范文本中的结束偏移")
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

class UploadWarning(BaseModel):
    """非致命解析/OCR 警告摘要。"""
    code: str = Field(..., description="警告码，如 ocr_page_failed")
    message: str = Field(..., description="面向用户的警告说明")
    location_label: str = Field(default="", description="关联来源位置标签")


class UploadResponse(BaseModel):
    """POST /api/upload 的返回体。"""
    message: str = Field(..., description="上传结果描述")
    document_id: str = Field(..., description="本次入库生成的稳定文档 ID")
    document_name: str = Field(..., description="文档文件名")
    chunk_count: int = Field(..., description="切分出的文本块数量")
    status: str = Field(default="success", description="处理状态：success / error")
    warnings: list[UploadWarning] = Field(
        default_factory=list,
        description="非致命解析/OCR 警告（如无文本页、OCR 失败）",
    )


# ─── 问答接口 ────────────────────────────────────────

class ChatRequest(BaseModel):
    """POST /api/chat 的请求体。"""
    question: str = Field(..., min_length=1, description="用户提出的自然语言问题")
    conversation_id: Optional[str] = None
    request_id: Optional[str] = None
    expected_last_message_id: Optional[str] = None
    retry_failed: bool = False
    tags: list[str] = Field(
        default_factory=list,
        description="按标签筛选检索范围（含子孙标签，多选取并集）；空列表表示全库",
    )


class Citation(BaseModel):
    """单条引用来源。"""
    source_id: int = Field(..., ge=1, description="回答中的来源编号，如【来源1】中的 1")
    chunk_id: Optional[str] = Field(default=None, description="引用对应的向量库文本块 ID")
    document_name: str = Field(..., description="引用的文档文件名")
    unit_id: Optional[str] = Field(default=None, description="结构化来源单元 ID")
    unit_index: Optional[int] = Field(default=None, ge=0, description="来源单元序号")
    locator_kind: Optional[str] = Field(default=None, description="来源定位类型")
    location_label: Optional[str] = Field(default=None, description="面向用户的来源位置")
    page_number: Optional[int] = Field(default=None, ge=1, description="真实页或逻辑页")
    slide_number: Optional[int] = Field(default=None, ge=1, description="幻灯片编号")
    heading_path: list[str] = Field(default_factory=list, description="来源标题路径")
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


class StructuredPreviewUnit(BaseModel):
    unit_id: str
    location: SourceLocator
    canonical_text: str
    blocks: list[LocatedBlock]

    @model_validator(mode="after")
    def validate_unit(self):
        expected_text = "\n\n".join(block.text for block in self.blocks)
        if expected_text != self.canonical_text:
            raise ValueError("blocks 与 canonical_text 不一致")
        return self


class StructuredCitationPreview(BaseModel):
    """structured 整文档预览，不包含旧 parsed_page_text 或重复 chunk。"""
    document_id: str
    chunk_id: str
    document_name: str
    parser_version: str
    location: SourceLocator
    target_unit_id: str
    units: list[StructuredPreviewUnit]
    highlight_start: int = Field(..., ge=0)
    highlight_end: int = Field(..., ge=0)

    @model_validator(mode="after")
    def validate_preview(self):
        if not self.units:
            raise ValueError("预览必须包含至少一个 unit")
        unit_ids = [unit.unit_id for unit in self.units]
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("预览 unit_id 不能重复")
        target = next(
            (
                unit for unit in self.units
                if unit.unit_id == self.target_unit_id
            ),
            None,
        )
        if target is None:
            raise ValueError("target_unit_id 不在预览 units 中")
        if target.location != self.location:
            raise ValueError("目标 location 与 target unit 不一致")
        if self.highlight_end <= self.highlight_start:
            raise ValueError("highlight_end 必须大于 highlight_start")
        if self.highlight_end > len(target.canonical_text):
            raise ValueError("highlight offset 超出 canonical_text")
        return self


class RetrievalTraceChunk(BaseModel):
    """Trace 中按 chunk_id 去重保存的完整候选原文。"""
    chunk_id: str
    document_name: str
    location_label: str = ""
    page_number: Optional[int] = None
    slide_number: Optional[int] = None
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
    followup_rewrite: Optional[float] = Field(default=None, ge=0)
    citation_extraction: Optional[float] = Field(default=None, ge=0)
    persistence: Optional[float] = Field(default=None, ge=0)
    time_to_first_delta: Optional[float] = Field(default=None, ge=0)
    delta_window: Optional[float] = Field(default=None, ge=0)
    total: float = 0.0


class RetrievalTraceEffectiveConfig(BaseModel):
    """本次检索实际使用的动态参数；不包含任何密钥或本机敏感值。"""

    model_config = ConfigDict(extra="forbid")

    retrieval_mode: str
    final_top_k: int
    dense_candidate_k: int
    dense_distance_threshold: float
    bm25_top_k: int
    bm25_k1: float
    bm25_b: float
    rrf_k: int
    reranker_enabled: bool
    reranker_candidate_k: int
    max_chunks_per_document: int
    refusal_gate_enabled: bool = False


class RetrievalTraceGenerationConfig(BaseModel):
    """一次模型调用用途的请求级生成配置；不包含原始思考文本。"""

    model_config = ConfigDict(extra="forbid")

    called: bool
    thinking_mode: Literal["provider_default", "enabled", "disabled"]
    reasoning_effort: Literal[
        "provider_default", "low", "medium", "high", "max"
    ]
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(ge=1)
    timeout_seconds: float = Field(ge=1)


class RetrievalTraceLLMSummary(BaseModel):
    """本次问答的模型调用摘要；禁止扩展为包含 API Key 的配置副本。"""

    model_config = ConfigDict(extra="forbid")

    called: bool
    provider: str
    model: str
    answer: Optional[RetrievalTraceGenerationConfig] = None
    rewrite: Optional[RetrievalTraceGenerationConfig] = None


class RetrievalTrace(BaseModel):
    """一次问答实际执行的完整检索链路。"""
    retrieval_mode: str
    reranker_enabled: bool
    timing_contract_version: Optional[Literal["chat-v2"]] = Field(
        default=None,
        description=(
            "聊天计量合同版本；chat-v2 的 total 为请求入口到持久化前，"
            "旧 Trace 为 null"
        ),
    )
    followup_rewrite_status: Optional[
        Literal["not_applicable", "disabled", "succeeded", "failed"]
    ] = Field(
        default=None,
        description="追问改写状态；旧 Trace 为 null",
    )
    settings_revision: Optional[int] = Field(
        default=None,
        ge=0,
        description="本次问答冻结的运行时设置版本；旧 Trace 为 null",
    )
    effective_config: Optional[RetrievalTraceEffectiveConfig] = Field(
        default=None,
        description="检索器实际执行的参数；旧 Trace 为 null",
    )
    llm: Optional[RetrievalTraceLLMSummary] = Field(
        default=None,
        description="模型是否实际调用及 provider/model；旧 Trace 为 null",
    )
    chunks: dict[str, RetrievalTraceChunk] = Field(default_factory=dict)
    stages: list[RetrievalTraceStage] = Field(default_factory=list)
    selected_chunk_ids: list[str] = Field(default_factory=list)
    context: RetrievalContextStats = Field(default_factory=RetrievalContextStats)
    timings_ms: RetrievalTraceTimings = Field(default_factory=RetrievalTraceTimings)
    tag_filter: list[str] = Field(
        default_factory=list,
        description="本次检索生效的标签筛选；空列表表示全库",
    )
    filtered_document_count: Optional[int] = Field(
        default=None,
        description="标签筛选换算出的 ready 文档数；未过滤时为 None",
    )
    sparse_backend: Optional[Literal["rank_bm25_memory", "bm25s_mmap"]] = None
    sparse_index_generation: Optional[str] = None
    sparse_manifest_hash: Optional[str] = None
    sparse_index_status: Optional[
        Literal["ready", "dirty", "missing", "corrupt", "mismatch"]
    ] = None
    sparse_fallback_reason: Optional[str] = None
    sparse_index_load_ms: float = Field(default=0.0, ge=0)
    sparse_contract_version: Optional[str] = None


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
    conversation_id: Optional[str] = None
    user_message_id: Optional[str] = None
    assistant_message_id: Optional[str] = None
    standalone_question: Optional[str] = None


class ConversationCreate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=120)


class ConversationUpdate(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)


class ConversationSummary(BaseModel):
    conversation_id: str
    title: str
    created_at: str
    updated_at: str


class ConversationMessage(BaseModel):
    message_id: str
    request_id: str
    sequence: int
    role: str
    content: str
    status: str
    standalone_question: Optional[str] = None
    citations: list[Citation] = Field(default_factory=list)
    trace: Optional[RetrievalTrace] = Field(
        default=None,
        description="助手消息的持久化检索 Trace；未开启或已淘汰时为 null",
    )
    created_at: str


class ConversationDetail(ConversationSummary):
    messages: list[ConversationMessage] = Field(default_factory=list)


# ─── 多级树形标签接口 ─────────────────────────────────

class TagInfo(BaseModel):
    """单个标签节点；树形结构由前端按 tag_path 前缀组装。"""
    tag_path: str = Field(..., description="如 '课程课件/数据结构'")
    name: str = Field(..., description="末段名称")
    direct_document_count: int = Field(
        default=0,
        description="直接挂在该标签上的 ready 文档数",
    )
    document_count: int = Field(
        default=0,
        description="该标签及其子孙标签命中的 ready 文档总数",
    )


class TagCreate(BaseModel):
    tag_path: str = Field(..., min_length=1)


class TagRename(BaseModel):
    new_path: str = Field(..., min_length=1)


class DocumentTagsUpdate(BaseModel):
    tags: list[str] = Field(default_factory=list, description="整体替换文档的标签集")


class DocumentPreview(BaseModel):
    """整份文档的结构化内容预览（知识库管理页）。"""
    document_id: str
    document_name: str
    tags: list[str] = Field(default_factory=list)
    parser_version: str
    unit_count: int = Field(..., ge=0)
    units: list[StructuredPreviewUnit] = Field(default_factory=list)
