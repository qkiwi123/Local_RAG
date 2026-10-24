"""运行时设置的内部强类型模型与只读 API 响应。

所有模型都拒绝未知字段；有效快照冻结，供一次请求在链路内稳定传递。
只读响应不包含 SecretStr，只暴露密钥是否配置、来源和安全后缀。
"""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)


ProviderName = Literal["deepseek", "openai_compatible"]
ThinkingMode = Literal["provider_default", "enabled", "disabled"]
ReasoningEffort = Literal["provider_default", "low", "medium", "high", "max"]
RetrievalMode = Literal["dense", "bm25", "hybrid"]
FieldSource = Literal["environment", "runtime", "system"]
ActivationLevel = Literal[
    "next_request",
    "next_document_upload",
    "restart_required",
    "reindex_required",
    "unavailable",
]


def _validate_http_url(value: str) -> str:
    clean = value.strip()
    if len(clean) > 2048:
        raise ValueError("Base URL 不能超过 2048 个字符")
    parsed = urlsplit(clean)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Base URL 必须是有效的 HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Base URL 不能包含用户名或密码")
    if parsed.query or parsed.fragment:
        raise ValueError("Base URL 不能包含查询参数或片段")
    return clean.rstrip("/")


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LLMSettings(_StrictFrozenModel):
    enabled: bool = True
    provider: ProviderName = "deepseek"
    base_url: str
    model: str = Field(min_length=1, max_length=200)
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(ge=1, le=32768)
    timeout_seconds: float = Field(ge=1, le=600)
    answer_thinking_mode: ThinkingMode = "disabled"
    answer_reasoning_effort: ReasoningEffort = "provider_default"
    rewrite_enabled: bool = True
    rewrite_thinking_mode: ThinkingMode = "disabled"
    rewrite_reasoning_effort: ReasoningEffort = "provider_default"
    rewrite_temperature: float = Field(default=0, ge=0, le=2)
    rewrite_max_tokens: int = Field(default=200, ge=1, le=32768)
    rewrite_timeout_seconds: float = Field(default=30, ge=1, le=600)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return _validate_http_url(value)

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("模型名不能为空")
        return clean


class VisionSettings(_StrictFrozenModel):
    enabled: bool = False
    source: Literal["follow_llm", "custom"] = "follow_llm"
    provider: ProviderName = "openai_compatible"
    base_url: str = ""
    model: str = Field(default="glm-4.6v-flash", min_length=1, max_length=200)
    pdf_scanned_enabled: bool = False
    pdf_page_images_enabled: bool = False
    embedded_images_enabled: bool = False
    recognize_thinking_mode: ThinkingMode = "disabled"
    recognize_reasoning_effort: ReasoningEffort = "provider_default"
    recognize_temperature: float = Field(default=0, ge=0, le=2)
    recognize_max_tokens: int = Field(default=4000, ge=1, le=32768)
    recognize_timeout_seconds: float = Field(default=60, ge=1, le=600)
    describe_thinking_mode: ThinkingMode = "disabled"
    describe_reasoning_effort: ReasoningEffort = "provider_default"
    describe_temperature: float = Field(default=0, ge=0, le=2)
    describe_max_tokens: int = Field(default=800, ge=1, le=32768)
    describe_timeout_seconds: float = Field(default=60, ge=1, le=600)
    max_image_edge: int = Field(default=1600, ge=256, le=4096)
    max_calls_per_document: int = Field(default=20, ge=1, le=500)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return _validate_http_url(value) if value.strip() else ""

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("视觉模型名不能为空")
        return value.strip()


class RefusalGateSettings(_StrictFrozenModel):
    available: bool = False
    enabled: bool = False
    reason: str = "pending_v3_5_calibration"

    @model_validator(mode="after")
    def reject_unavailable_enable(self) -> "RefusalGateSettings":
        if self.enabled:
            raise ValueError("refusal gate 在 V3-5 校准完成前不可启用")
        return self


class RetrievalSettings(_StrictFrozenModel):
    trace_enabled: bool = False
    mode: RetrievalMode
    final_top_k: int = Field(ge=1, le=20)
    dense_candidate_k: int = Field(ge=1, le=100)
    dense_distance_threshold: float = Field(ge=0, le=2)
    bm25_top_k: int = Field(ge=1, le=100)
    bm25_k1: float = Field(gt=0)
    bm25_b: float = Field(ge=0, le=1)
    rrf_k: int = Field(ge=1, le=200)
    reranker_enabled: bool
    reranker_candidate_k: int = Field(ge=1, le=100)
    max_chunks_per_document: int = Field(ge=1)
    refusal_gate: RefusalGateSettings = Field(
        default_factory=RefusalGateSettings
    )

    @model_validator(mode="after")
    def validate_candidate_relationships(self) -> "RetrievalSettings":
        if self.dense_candidate_k < self.final_top_k:
            raise ValueError("dense_candidate_k 不能小于 final_top_k")
        if self.bm25_top_k < self.final_top_k:
            raise ValueError("bm25_top_k 不能小于 final_top_k")
        if self.reranker_candidate_k < self.final_top_k:
            raise ValueError("reranker_candidate_k 不能小于 final_top_k")
        if self.max_chunks_per_document > self.final_top_k:
            raise ValueError("max_chunks_per_document 不能大于 final_top_k")
        return self


class ChunkingSettings(_StrictFrozenModel):
    strategy: Literal["fixed", "recursive"] = "fixed"
    chunk_size: int = Field(default=500, gt=0, strict=True)
    chunk_overlap: int = Field(default=50, ge=0, strict=True)
    max_tokens: int = Field(default=480, gt=0, strict=True)

    @model_validator(mode="after")
    def validate_overlap(self) -> "ChunkingSettings":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("重叠长度必须小于分块长度")
        return self


class EffectiveSettingsSnapshot(_StrictFrozenModel):
    revision: int = Field(ge=0)
    updated_at: str | None = None
    llm: LLMSettings
    vision: VisionSettings = Field(default_factory=VisionSettings)
    vision_api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    vision_api_key_source: Literal["environment", "runtime_secret", "none"] = "none"
    retrieval: RetrievalSettings
    chunking: ChunkingSettings = Field(default_factory=ChunkingSettings)
    api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    api_key_source: Literal["environment", "runtime_secret", "none"]
    field_sources: dict[str, FieldSource]


class APIKeyStatus(_StrictFrozenModel):
    configured: bool
    source: Literal["environment", "runtime", "none"]
    masked_suffix: str | None = None


class VisionConnectionView(_StrictFrozenModel):
    provider: ProviderName
    base_url: str
    model: str
    api_key: APIKeyStatus


class VisionSettingsView(VisionSettings):
    api_key: APIKeyStatus
    effective_connection: VisionConnectionView


class LLMSettingsView(_StrictFrozenModel):
    enabled: bool
    provider: ProviderName
    base_url: str
    model: str
    api_key: APIKeyStatus
    temperature: float
    max_tokens: int
    timeout_seconds: float
    answer_thinking_mode: ThinkingMode
    answer_reasoning_effort: ReasoningEffort
    rewrite_enabled: bool
    rewrite_thinking_mode: ThinkingMode
    rewrite_reasoning_effort: ReasoningEffort
    rewrite_temperature: float
    rewrite_max_tokens: int
    rewrite_timeout_seconds: float


class SettingsRuntimeSummary(_StrictFrozenModel):
    access_scope: Literal["trusted_local_workspace"] = "trusted_local_workspace"
    llm_activation: ActivationLevel = "next_request"
    vision_activation: ActivationLevel = "next_document_upload"
    retrieval_activation: ActivationLevel = "next_request"
    chunking_activation: ActivationLevel = "next_document_upload"
    refusal_gate_activation: ActivationLevel = "unavailable"


class SettingsReadResponse(_StrictFrozenModel):
    revision: int = Field(ge=0)
    updated_at: str | None = None
    llm: LLMSettingsView
    vision: VisionSettingsView
    retrieval: RetrievalSettings
    chunking: ChunkingSettings
    runtime: SettingsRuntimeSummary
    field_sources: dict[str, FieldSource]
    field_effects: dict[str, ActivationLevel]


class APIKeyReplace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["replace"]
    value: SecretStr

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: SecretStr) -> SecretStr:
        clean = value.get_secret_value().strip()
        if not clean:
            raise ValueError("API Key 不能为空")
        if len(clean) > 8192:
            raise ValueError("API Key 长度不能超过 8192 个字符")
        return SecretStr(clean)


class APIKeyClear(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["clear"]


APIKeyUpdate = Annotated[
    APIKeyReplace | APIKeyClear,
    Field(discriminator="action"),
]


class LLMSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    provider: ProviderName | None = None
    base_url: str | None = None
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=32768)
    timeout_seconds: float | None = Field(default=None, ge=1, le=600)
    answer_thinking_mode: ThinkingMode | None = None
    answer_reasoning_effort: ReasoningEffort | None = None
    rewrite_enabled: bool | None = None
    rewrite_thinking_mode: ThinkingMode | None = None
    rewrite_reasoning_effort: ReasoningEffort | None = None
    rewrite_temperature: float | None = Field(default=None, ge=0, le=2)
    rewrite_max_tokens: int | None = Field(default=None, ge=1, le=32768)
    rewrite_timeout_seconds: float | None = Field(default=None, ge=1, le=600)
    api_key_update: APIKeyUpdate | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_null_setting_values(cls, value):
        if isinstance(value, dict):
            null_fields = sorted(
                key
                for key, item in value.items()
                if item is None and key != "api_key_update"
            )
            if null_fields:
                raise ValueError(
                    "LLM 设置不接受 null: " + ", ".join(null_fields)
                )
        return value

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        return _validate_http_url(value) if value is not None else None

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        if not clean or len(clean) > 200:
            raise ValueError("模型名长度必须为 1 到 200 个字符")
        return clean

    def runtime_payload(self) -> dict:
        return self.model_dump(
            exclude={"api_key_update"},
            exclude_none=True,
            exclude_unset=True,
        )


class LLMConnectionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ProviderName | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: SecretStr | None = None
    timeout_seconds: float | None = Field(default=None, ge=1, le=600)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        return _validate_http_url(value) if value is not None else None

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        if not clean or len(clean) > 200:
            raise ValueError("模型名长度必须为 1 到 200 个字符")
        return clean

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        clean = value.get_secret_value().strip()
        if not clean:
            raise ValueError("API Key 不能为空")
        if len(clean) > 8192:
            raise ValueError("API Key 长度不能超过 8192 个字符")
        return SecretStr(clean)


class LLMConnectionTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm: LLMConnectionDraft = Field(default_factory=LLMConnectionDraft)


class LLMConnectionTestResponse(_StrictFrozenModel):
    success: bool
    latency_ms: float = Field(ge=0)
    provider: ProviderName
    model: str
    endpoint: str
    error_code: Literal[
        "authentication_failed",
        "permission_denied",
        "model_not_found",
        "timeout",
        "network_error",
        "upstream_error",
    ] | None = None
    message: str


class ModelRuntimeStatus(_StrictFrozenModel):
    model: str
    enabled: bool
    configured_device: str
    actual_device: str | None = None
    loaded: bool
    verified: bool
    cache_available: bool
    batch_size: int = Field(ge=1)
    fp16: bool | None = None
    verification_error: Literal[
        "cached_model_unavailable",
        "actual_device_unavailable",
        "device_mismatch",
    ] | None = None


class TorchRuntimeStatus(_StrictFrozenModel):
    version: str | None = None
    cuda_available: bool
    cuda_runtime: str | None = None
    gpu_names: list[str] = Field(default_factory=list)


class IndexRuntimeStatus(_StrictFrozenModel):
    parser_version: str
    chunk_strategy: str
    chunk_size: int = Field(gt=0)
    chunk_overlap: int = Field(ge=0)
    chunk_max_tokens: int = Field(gt=0)
    storage_profile: Literal["production", "test"]
    chroma_directory: str
    metadata_database: str
    upload_directory: str
    process_index_revision: int = Field(ge=0)
    sparse_backend: Literal["rank_bm25_memory", "bm25s_mmap"]
    sparse_index_status: Literal["ready", "dirty", "missing", "corrupt", "mismatch"]
    sparse_index_directory: str
    sparse_index_generation: str | None = None
    sparse_manifest_hash: str | None = None
    sparse_index_load_ms: float = Field(default=0.0, ge=0)
    sparse_contract_version: str | None = None
    # P6-A后台重建协调器快照；字段带默认值，旧客户端读取不受影响。
    sparse_rebuild_enabled: bool = False
    sparse_rebuild_pending: bool = False
    sparse_rebuild_running: bool = False
    sparse_rebuild_last_outcome: str | None = None


class SettingsRuntimeStatusResponse(_StrictFrozenModel):
    revision: int = Field(ge=0)
    updated_at: str | None = None
    embedding: ModelRuntimeStatus
    reranker: ModelRuntimeStatus
    torch: TorchRuntimeStatus
    index: IndexRuntimeStatus


class _PartialModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_nulls(cls, value):
        if isinstance(value, dict):
            null_fields = sorted(key for key, item in value.items() if item is None)
            if null_fields:
                raise ValueError(
                    "运行时覆盖不接受 null: " + ", ".join(null_fields)
                )
        return value


class LLMSettingsOverrides(_PartialModel):
    enabled: bool | None = None
    provider: ProviderName | None = None
    base_url: str | None = None
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=32768)
    timeout_seconds: float | None = Field(default=None, ge=1, le=600)
    answer_thinking_mode: ThinkingMode | None = None
    answer_reasoning_effort: ReasoningEffort | None = None
    rewrite_enabled: bool | None = None
    rewrite_thinking_mode: ThinkingMode | None = None
    rewrite_reasoning_effort: ReasoningEffort | None = None
    rewrite_temperature: float | None = Field(default=None, ge=0, le=2)
    rewrite_max_tokens: int | None = Field(default=None, ge=1, le=32768)
    rewrite_timeout_seconds: float | None = Field(default=None, ge=1, le=600)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        return _validate_http_url(value) if value is not None else None

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        if not clean or len(clean) > 200:
            raise ValueError("模型名长度必须为 1 到 200 个字符")
        return clean


class RefusalGateOverrides(_PartialModel):
    enabled: bool | None = None

    @field_validator("enabled")
    @classmethod
    def reject_enable(cls, value: bool | None) -> bool | None:
        if value:
            raise ValueError("refusal gate 在 V3-5 校准完成前不可启用")
        return value


class RetrievalSettingsOverrides(_PartialModel):
    trace_enabled: bool | None = None
    mode: RetrievalMode | None = None
    final_top_k: int | None = Field(default=None, ge=1, le=20)
    dense_candidate_k: int | None = Field(default=None, ge=1, le=100)
    dense_distance_threshold: float | None = Field(default=None, ge=0, le=2)
    bm25_top_k: int | None = Field(default=None, ge=1, le=100)
    bm25_k1: float | None = Field(default=None, gt=0)
    bm25_b: float | None = Field(default=None, ge=0, le=1)
    rrf_k: int | None = Field(default=None, ge=1, le=200)
    reranker_enabled: bool | None = None
    reranker_candidate_k: int | None = Field(default=None, ge=1, le=100)
    max_chunks_per_document: int | None = Field(default=None, ge=1)
    refusal_gate: RefusalGateOverrides | None = None

    def runtime_payload(self) -> dict:
        return self.model_dump(exclude_none=True, exclude_unset=True)


class ChunkingSettingsOverrides(_PartialModel):
    strategy: Literal["fixed", "recursive"] | None = None
    chunk_size: int | None = Field(default=None, gt=0, strict=True)
    chunk_overlap: int | None = Field(default=None, ge=0, strict=True)
    max_tokens: int | None = Field(default=None, gt=0, strict=True)

    def runtime_payload(self) -> dict:
        return self.model_dump(exclude_none=True, exclude_unset=True)


class VisionSettingsOverrides(_PartialModel):
    enabled: bool | None = None
    source: Literal["follow_llm", "custom"] | None = None
    provider: ProviderName | None = None
    base_url: str | None = None
    model: str | None = Field(default=None, min_length=1, max_length=200)
    pdf_scanned_enabled: bool | None = None
    pdf_page_images_enabled: bool | None = None
    embedded_images_enabled: bool | None = None
    recognize_thinking_mode: ThinkingMode | None = None
    recognize_reasoning_effort: ReasoningEffort | None = None
    recognize_temperature: float | None = Field(default=None, ge=0, le=2)
    recognize_max_tokens: int | None = Field(default=None, ge=1, le=32768)
    recognize_timeout_seconds: float | None = Field(default=None, ge=1, le=600)
    describe_thinking_mode: ThinkingMode | None = None
    describe_reasoning_effort: ReasoningEffort | None = None
    describe_temperature: float | None = Field(default=None, ge=0, le=2)
    describe_max_tokens: int | None = Field(default=None, ge=1, le=32768)
    describe_timeout_seconds: float | None = Field(default=None, ge=1, le=600)
    max_image_edge: int | None = Field(default=None, ge=256, le=4096)
    max_calls_per_document: int | None = Field(default=None, ge=1, le=500)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_http_url(value) if value.strip() else ""

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("视觉模型名不能为空")
        return value.strip() if value is not None else None

    def runtime_payload(self) -> dict:
        return self.model_dump(exclude={"api_key_update"}, exclude_none=True, exclude_unset=True)


class VisionSettingsPatch(VisionSettingsOverrides):
    api_key_update: APIKeyUpdate | None = None


class VisionConnectionTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vision: VisionSettingsPatch = Field(default_factory=VisionSettingsPatch)
    task: Literal["recognize", "describe"] = "recognize"


class VisionConnectionTestResponse(_StrictFrozenModel):
    success: bool
    latency_ms: float = Field(ge=0)
    provider: ProviderName
    model: str
    endpoint: str
    error_code: str | None = None
    message: str
    request_id: str | None = None


class SettingsPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    llm: LLMSettingsPatch | None = None
    vision: VisionSettingsPatch | None = None
    retrieval: RetrievalSettingsOverrides | None = None
    chunking: ChunkingSettingsOverrides | None = None

    @model_validator(mode="after")
    def require_section(self) -> "SettingsPatchRequest":
        if all(section is None for section in (self.llm, self.retrieval, self.vision, self.chunking)):
            raise ValueError("至少需要提供 llm、vision、retrieval 或 chunking 设置")
        return self


class SettingsPatchResponse(_StrictFrozenModel):
    revision: int = Field(ge=0)
    settings: SettingsReadResponse
    changed_fields: list[str]
    effects: list[str]
    warnings: list[str]


class SettingsResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    section: Literal["llm", "vision", "retrieval", "chunking", "all"]


class RuntimeSettingsOverrides(_PartialModel):
    llm: LLMSettingsOverrides | None = None
    vision: VisionSettingsOverrides | None = None
    retrieval: RetrievalSettingsOverrides | None = None
    chunking: ChunkingSettingsOverrides | None = None

    def to_payload(self) -> dict:
        """返回只包含显式覆盖字段的 JSON 兼容字典。"""
        return self.model_dump(exclude_none=True, exclude_unset=True)
