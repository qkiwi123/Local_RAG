// 与后端 Pydantic schema 对齐的类型定义。

export interface ConversationSummary {
  conversation_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface Citation {
  source_id: number;
  chunk_id?: string | null;
  document_name: string;
  unit_id?: string | null;
  unit_index?: number | null;
  locator_kind?: string | null;
  location_label?: string | null;
  page_number?: number | null;
  slide_number?: number | null;
  heading_path: string[];
  excerpt: string;
  document_id?: string | null;
  parser_version?: string | null;
  content_type?: string | null;
  start_char?: number | null;
  end_char?: number | null;
  preview_available: boolean;
}

export interface RetrievalTrace {
  retrieval_mode: string;
  reranker_enabled: boolean;
  timing_contract_version?: "chat-v2" | null;
  followup_rewrite_status?: "not_applicable" | "disabled" | "succeeded" | "failed" | null;
  settings_revision?: number | null;
  effective_config?: {
    retrieval_mode: string;
    final_top_k: number;
    dense_candidate_k: number;
    dense_distance_threshold: number;
    bm25_top_k: number;
    bm25_k1: number;
    bm25_b: number;
    rrf_k: number;
    reranker_enabled: boolean;
    reranker_candidate_k: number;
    max_chunks_per_document: number;
    refusal_gate_enabled: boolean;
  } | null;
  llm?: {
    called: boolean;
    provider: string;
    model: string;
    answer?: {
      called: boolean;
      thinking_mode: ThinkingMode;
      reasoning_effort: ReasoningEffort;
      temperature: number;
      max_tokens: number;
      timeout_seconds: number;
    } | null;
    rewrite?: {
      called: boolean;
      thinking_mode: ThinkingMode;
      reasoning_effort: ReasoningEffort;
      temperature: number;
      max_tokens: number;
      timeout_seconds: number;
    } | null;
  } | null;
  chunks: Record<string, { chunk_id: string; document_name: string; location_label?: string; page_number?: number | null; slide_number?: number | null; content: string }>;
  stages: Array<{ name: string; candidates: Array<Record<string, unknown>> }>;
  selected_chunk_ids: string[];
  context: { chunk_count: number; char_count: number; token_count: number };
  timings_ms: {
    query_embedding?: number;
    dense_retrieval?: number;
    sparse_retrieval?: number;
    rrf?: number;
    rerank?: number;
    context_formatting?: number;
    llm_generation?: number;
    followup_rewrite?: number | null;
    citation_extraction?: number | null;
    persistence?: number | null;
    time_to_first_delta?: number | null;
    delta_window?: number | null;
    total?: number;
    [key: string]: number | null | undefined;
  };
  tag_filter: string[];
  filtered_document_count?: number | null;
  sparse_backend?: "rank_bm25_memory" | "bm25s_mmap" | null;
  sparse_index_generation?: string | null;
  sparse_manifest_hash?: string | null;
  sparse_index_status?: "ready" | "dirty" | "missing" | "corrupt" | "mismatch" | null;
  sparse_fallback_reason?: string | null;
  sparse_index_load_ms?: number;
  sparse_contract_version?: string | null;
}

export interface ConversationMessage {
  message_id: string;
  request_id: string;
  sequence: number;
  role: "user" | "assistant";
  content: string;
  status: "pending" | "complete" | "failed";
  standalone_question?: string | null;
  citations: Citation[];
  trace?: RetrievalTrace | null;
  created_at: string;
}

export interface ConversationDetail extends ConversationSummary {
  messages: ConversationMessage[];
}

export interface TagInfo {
  tag_path: string;
  name: string;
  direct_document_count: number;
  document_count: number;
}

export interface DocumentItem {
  document_id: string;
  original_name: string;
  stored_name: string;
  size_bytes: number;
  status: string;
  chunk_count: number;
  error?: string | null;
  created_at: string;
  updated_at: string;
  tags: string[];
}

export interface LocatedBlock {
  block_id: string;
  block_type: "heading" | "paragraph" | "list_item" | "table" | "code" | "quote";
  text: string;
  sequence: number;
  heading_level?: number | null;
  list_level?: number | null;
  table_rows?: string[][] | null;
  attributes: Record<string, string | number | boolean>;
  start_char: number;
  end_char: number;
}

export interface SourceLocator {
  kind: string;
  unit_index: number;
  location_label: string;
  page_number?: number | null;
  slide_number?: number | null;
  heading_path: string[];
}

export interface PreviewUnit {
  unit_id: string;
  location: SourceLocator;
  canonical_text: string;
  blocks: LocatedBlock[];
}

export interface DocumentPreview {
  document_id: string;
  document_name: string;
  tags: string[];
  parser_version: string;
  unit_count: number;
  units: PreviewUnit[];
}

export interface CitationPreviewPayload {
  document_id: string;
  chunk_id: string;
  document_name: string;
  parser_version: string;
  location: SourceLocator;
  target_unit_id: string;
  units: PreviewUnit[];
  highlight_start: number;
  highlight_end: number;
}

export interface UploadWarning {
  code: string;
  message: string;
  location_label: string;
}

export interface UploadResponse {
  message: string;
  document_id: string;
  document_name: string;
  chunk_count: number;
  status: string;
  warnings: UploadWarning[];
}

export type SettingsFieldSource = "environment" | "runtime" | "system";
export type SettingsActivation =
  | "next_request"
  | "restart_required"
  | "reindex_required"
  | "unavailable";

export interface ApiKeyStatus {
  configured: boolean;
  source: "environment" | "runtime" | "none";
  masked_suffix?: string | null;
}

export type ThinkingMode = "provider_default" | "enabled" | "disabled";
export type ReasoningEffort =
  | "provider_default"
  | "low"
  | "medium"
  | "high"
  | "max";

export interface LlmSettings {
  enabled: boolean;
  provider: "deepseek" | "openai_compatible";
  base_url: string;
  model: string;
  api_key: ApiKeyStatus;
  temperature: number;
  max_tokens: number;
  timeout_seconds: number;
  answer_thinking_mode: ThinkingMode;
  answer_reasoning_effort: ReasoningEffort;
  rewrite_enabled: boolean;
  rewrite_thinking_mode: ThinkingMode;
  rewrite_reasoning_effort: ReasoningEffort;
  rewrite_temperature: number;
  rewrite_max_tokens: number;
  rewrite_timeout_seconds: number;
}

export interface RefusalGateSettings {
  available: boolean;
  enabled: boolean;
  reason: string;
}

export interface RetrievalSettings {
  mode: "dense" | "bm25" | "hybrid";
  final_top_k: number;
  dense_candidate_k: number;
  dense_distance_threshold: number;
  bm25_top_k: number;
  bm25_k1: number;
  bm25_b: number;
  rrf_k: number;
  reranker_enabled: boolean;
  reranker_candidate_k: number;
  max_chunks_per_document: number;
  refusal_gate: RefusalGateSettings;
}

export interface SettingsReadResponse {
  revision: number;
  updated_at?: string | null;
  llm: LlmSettings;
  retrieval: RetrievalSettings;
  runtime: {
    access_scope: "trusted_local_workspace";
    llm_activation: SettingsActivation;
    retrieval_activation: SettingsActivation;
    refusal_gate_activation: SettingsActivation;
  };
  field_sources: Record<string, SettingsFieldSource>;
  field_effects: Record<string, SettingsActivation>;
}

export interface LlmSettingsPatch {
  enabled?: boolean;
  provider?: "deepseek" | "openai_compatible";
  base_url?: string;
  model?: string;
  temperature?: number;
  max_tokens?: number;
  timeout_seconds?: number;
  answer_thinking_mode?: ThinkingMode;
  answer_reasoning_effort?: ReasoningEffort;
  rewrite_enabled?: boolean;
  rewrite_thinking_mode?: ThinkingMode;
  rewrite_reasoning_effort?: ReasoningEffort;
  rewrite_temperature?: number;
  rewrite_max_tokens?: number;
  rewrite_timeout_seconds?: number;
  api_key_update?:
    | { action: "replace"; value: string }
    | { action: "clear" };
}

export interface RetrievalSettingsPatch {
  mode?: "dense" | "bm25" | "hybrid";
  final_top_k?: number;
  dense_candidate_k?: number;
  dense_distance_threshold?: number;
  bm25_top_k?: number;
  bm25_k1?: number;
  bm25_b?: number;
  rrf_k?: number;
  reranker_enabled?: boolean;
  reranker_candidate_k?: number;
  max_chunks_per_document?: number;
}

export interface SettingsPatchResponse {
  revision: number;
  settings: SettingsReadResponse;
  changed_fields: string[];
  effects: string[];
  warnings: string[];
}

export interface LlmConnectionTestResponse {
  success: boolean;
  latency_ms: number;
  provider: "deepseek" | "openai_compatible";
  model: string;
  endpoint: string;
  error_code?: string | null;
  message: string;
}

export interface ModelRuntimeStatus {
  model: string;
  enabled: boolean;
  configured_device: string;
  actual_device?: string | null;
  loaded: boolean;
  verified: boolean;
  cache_available: boolean;
  batch_size: number;
  fp16?: boolean | null;
  verification_error?: string | null;
}

export interface SettingsRuntimeStatus {
  revision: number;
  updated_at?: string | null;
  embedding: ModelRuntimeStatus;
  reranker: ModelRuntimeStatus;
  torch: {
    version?: string | null;
    cuda_available: boolean;
    cuda_runtime?: string | null;
    gpu_names: string[];
  };
  index: {
    parser_version: string;
    chunk_strategy: string;
    chunk_size: number;
    chunk_overlap: number;
    chunk_max_tokens: number;
    storage_profile: "production" | "test";
    chroma_directory: string;
    metadata_database: string;
    upload_directory: string;
    process_index_revision: number;
    sparse_backend: "rank_bm25_memory" | "bm25s_mmap";
    sparse_index_status: "ready" | "dirty" | "missing" | "corrupt" | "mismatch";
    sparse_index_directory: string;
    sparse_index_generation?: string | null;
    sparse_manifest_hash?: string | null;
    sparse_index_load_ms: number;
    sparse_contract_version?: string | null;
  };
}

export type StreamEvent =
  | { event: "meta"; conversation_id?: string; request_id: string; user_message_id?: string; assistant_message_id?: string; standalone_question: string }
  | { event: "delta"; text: string }
  | { event: "citations"; citations: Citation[] }
  | { event: "done"; assistant_message_id?: string; trace?: RetrievalTrace | null }
  | { event: "error"; code: string; message: string };
