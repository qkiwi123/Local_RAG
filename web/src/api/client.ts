import type {
  ConversationDetail,
  ConversationSummary,
  CitationPreviewPayload,
  DocumentItem,
  DocumentPreview,
  TagInfo,
  UploadResponse,
  LlmConnectionTestResponse,
  LlmSettingsPatch,
  RetrievalSettingsPatch,
  SettingsPatchResponse,
  SettingsReadResponse,
  SettingsRuntimeStatus,
} from "./types";

export class ApiError extends Error {
  status: number;
  code?: string;

  constructor(status: number, message: string, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    let detail: unknown = null;
    try {
      detail = (await response.json())?.detail;
    } catch {
      // 非 JSON 响应
    }
    let message = `HTTP ${response.status}`;
    let code: string | undefined;
    if (typeof detail === "string" && detail) {
      message = detail;
    } else if (Array.isArray(detail)) {
      const messages = detail
        .map((item) =>
          item && typeof item === "object" && "msg" in item
            ? String(item.msg)
            : "",
        )
        .filter(Boolean);
      message = messages.join("；") || message;
    } else if (detail && typeof detail === "object") {
      const item = detail as { code?: string; message?: string };
      code = item.code;
      message = item.message || message;
    }
    throw new ApiError(response.status, message, code);
  }
  return (await response.json()) as T;
}

function jsonInit(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

export const api = {
  // 运行时设置
  getSettings: () => request<SettingsReadResponse>("/api/settings"),
  updateSettings: (payload: {
    expected_revision: number;
    llm?: LlmSettingsPatch;
    retrieval?: RetrievalSettingsPatch;
  }) =>
    request<SettingsPatchResponse>(
      "/api/settings",
      jsonInit("PATCH", payload),
    ),
  resetSettings: (
    expected_revision: number,
    section: "llm" | "retrieval" | "all",
  ) =>
    request<SettingsPatchResponse>(
      "/api/settings/reset",
      jsonInit("POST", { expected_revision, section }),
    ),
  testLlmConnection: (llm: {
    provider?: "deepseek" | "openai_compatible";
    base_url?: string;
    model?: string;
    api_key?: string;
    timeout_seconds?: number;
  }) =>
    request<LlmConnectionTestResponse>(
      "/api/settings/test-llm",
      jsonInit("POST", { llm }),
    ),
  getSettingsRuntimeStatus: () =>
    request<SettingsRuntimeStatus>("/api/settings/runtime-status"),

  // 会话
  listConversations: () =>
    request<ConversationSummary[]>("/api/conversations"),
  createConversation: () =>
    request<ConversationDetail>("/api/conversations", jsonInit("POST", {})),
  getConversation: (id: string) =>
    request<ConversationDetail>(`/api/conversations/${id}`),
  renameConversation: (id: string, title: string) =>
    request<ConversationDetail>(
      `/api/conversations/${id}`,
      jsonInit("PATCH", { title }),
    ),
  deleteConversation: (id: string) =>
    request<{ status: string }>(`/api/conversations/${id}`, {
      method: "DELETE",
    }),

  // 标签
  listTags: () => request<TagInfo[]>("/api/tags"),
  createTag: (tag_path: string) =>
    request<TagInfo>("/api/tags", jsonInit("POST", { tag_path })),
  renameTag: (oldPath: string, newPath: string) =>
    request<{ status: string }>(
      `/api/tags/${encodeURIComponent(oldPath)}`,
      jsonInit("PATCH", { new_path: newPath }),
    ),
  deleteTag: (tagPath: string) =>
    request<{ status: string }>(
      `/api/tags/${encodeURIComponent(tagPath)}`,
      { method: "DELETE" },
    ),

  // 文档
  listDocuments: (q?: string, tags?: string[]) => {
    const params = [
      ...(q?.trim() ? [`q=${encodeURIComponent(q.trim())}`] : []),
      ...(tags ?? []).map((tag) => `tags=${encodeURIComponent(tag)}`),
    ].join("&");
    return request<{ documents: string[]; document_items: DocumentItem[] }>(
      `/api/documents${params ? `?${params}` : ""}`,
    );
  },
  uploadDocument: (file: File, tags: string[]) => {
    const form = new FormData();
    form.append("file", file);
    for (const tag of tags) {
      form.append("tags", tag);
    }
    return request<UploadResponse>("/api/upload", { method: "POST", body: form });
  },
  setDocumentTags: (documentId: string, tags: string[]) =>
    request<{ status: string; tags: string[] }>(
      `/api/documents/id/${documentId}/tags`,
      jsonInit("PUT", { tags }),
    ),
  deleteDocument: (documentId: string) =>
    request<{ status: string }>(`/api/documents/id/${documentId}`, {
      method: "DELETE",
    }),
  getDocumentPreview: (documentId: string) =>
    request<DocumentPreview>(`/api/documents/id/${documentId}/preview`),

  // 引用预览
  getCitationPreview: (documentId: string, chunkId: string) =>
    request<CitationPreviewPayload>(
      `/api/citations/${documentId}/chunks/${chunkId}/preview`,
    ),

  // OCR 原图对照预览
  ocrImageUrl: (documentId: string, imageRef: string) =>
    `/api/documents/id/${encodeURIComponent(documentId)}/ocr-images/${encodeURIComponent(imageRef)}`,
};
