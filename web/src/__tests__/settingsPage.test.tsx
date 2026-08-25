import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { message } from "antd";
import type {
  SettingsReadResponse,
  SettingsRuntimeStatus,
} from "../api/types";
import SettingsPage from "../pages/settings/SettingsPage";
import { ApiError } from "../api/client";

const mocks = vi.hoisted(() => ({
  getSettings: vi.fn(),
  updateSettings: vi.fn(),
  resetSettings: vi.fn(),
  testLlmConnection: vi.fn(),
  getSettingsRuntimeStatus: vi.fn(),
}));

vi.mock("../api/client", () => ({
  api: mocks,
  ApiError: class ApiError extends Error {
    status: number;
    code?: string;

    constructor(status: number, message: string, code?: string) {
      super(message);
      this.status = status;
      this.code = code;
    }
  },
}));

const settings: SettingsReadResponse = {
  revision: 3,
  updated_at: "2026-08-24T10:00:00+08:00",
  llm: {
    enabled: true,
    provider: "deepseek",
    base_url: "https://api.deepseek.com",
    model: "deepseek-chat",
    api_key: {
      configured: true,
      source: "runtime",
      masked_suffix: "2468",
    },
    temperature: 0.3,
    max_tokens: 2000,
    timeout_seconds: 120,
  },
  retrieval: {
    mode: "hybrid",
    final_top_k: 5,
    dense_candidate_k: 15,
    dense_distance_threshold: 0.67,
    bm25_top_k: 15,
    bm25_k1: 1.5,
    bm25_b: 0.75,
    rrf_k: 60,
    reranker_enabled: true,
    reranker_candidate_k: 15,
    max_chunks_per_document: 3,
    refusal_gate: {
      available: false,
      enabled: false,
      reason: "pending_v3_5_calibration",
    },
  },
  runtime: {
    access_scope: "trusted_local_workspace",
    llm_activation: "next_request",
    retrieval_activation: "next_request",
    refusal_gate_activation: "unavailable",
  },
  field_sources: {
    "llm.enabled": "environment",
    "llm.provider": "environment",
    "llm.base_url": "environment",
    "llm.model": "runtime",
    "llm.api_key": "runtime",
    "llm.temperature": "environment",
    "llm.max_tokens": "environment",
    "llm.timeout_seconds": "environment",
    "retrieval.mode": "runtime",
    "retrieval.final_top_k": "runtime",
    "retrieval.dense_candidate_k": "environment",
    "retrieval.dense_distance_threshold": "environment",
    "retrieval.bm25_top_k": "environment",
    "retrieval.bm25_k1": "environment",
    "retrieval.bm25_b": "environment",
    "retrieval.rrf_k": "environment",
    "retrieval.reranker_enabled": "environment",
    "retrieval.reranker_candidate_k": "environment",
    "retrieval.max_chunks_per_document": "environment",
  },
  field_effects: {
    "llm.enabled": "next_request",
    "llm.provider": "next_request",
    "llm.base_url": "next_request",
    "llm.model": "next_request",
    "llm.api_key": "next_request",
    "llm.temperature": "next_request",
    "llm.max_tokens": "next_request",
    "llm.timeout_seconds": "next_request",
    "retrieval.mode": "next_request",
    "retrieval.final_top_k": "next_request",
    "retrieval.dense_candidate_k": "next_request",
    "retrieval.dense_distance_threshold": "next_request",
    "retrieval.bm25_top_k": "next_request",
    "retrieval.bm25_k1": "next_request",
    "retrieval.bm25_b": "next_request",
    "retrieval.rrf_k": "next_request",
    "retrieval.reranker_enabled": "next_request",
    "retrieval.reranker_candidate_k": "next_request",
    "retrieval.max_chunks_per_document": "next_request",
    "retrieval.refusal_gate.enabled": "unavailable",
  },
};

const status: SettingsRuntimeStatus = {
  revision: 3,
  updated_at: settings.updated_at,
  embedding: {
    model: "BAAI/bge-large-zh-v1.5",
    enabled: true,
    configured_device: "cuda:0",
    actual_device: "cuda:0",
    loaded: true,
    verified: true,
    cache_available: true,
    batch_size: 32,
  },
  reranker: {
    model: "BAAI/bge-reranker-v2-m3",
    enabled: true,
    configured_device: "cuda:0",
    actual_device: "cuda:0",
    loaded: true,
    verified: true,
    cache_available: true,
    batch_size: 8,
    fp16: true,
  },
  torch: {
    version: "2.8.0+cu128",
    cuda_available: true,
    cuda_runtime: "12.8",
    gpu_names: ["NVIDIA GPU"],
  },
  index: {
    parser_version: "structured-v2",
    chunk_strategy: "semantic",
    chunk_size: 500,
    chunk_overlap: 80,
    chunk_max_tokens: 450,
    storage_profile: "production",
    chroma_directory: "chroma_db",
    metadata_database: "metadata.db",
    upload_directory: "uploads",
    process_index_revision: 9,
  },
};

function response(nextSettings: SettingsReadResponse = settings) {
  return {
    revision: nextSettings.revision,
    settings: nextSettings,
    changed_fields: [],
    effects: ["next_request"],
    warnings: [],
  };
}

describe("SettingsPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
    vi.stubGlobal("matchMedia", () => ({
      matches: false,
      addListener() {},
      removeListener() {},
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent: () => false,
    }));
    vi.stubGlobal("confirm", vi.fn(() => true));
    mocks.getSettings.mockResolvedValue(settings);
    mocks.getSettingsRuntimeStatus.mockResolvedValue(status);
    mocks.updateSettings.mockResolvedValue(response());
    mocks.resetSettings.mockResolvedValue(response());
    mocks.testLlmConnection.mockResolvedValue({
      success: true,
      latency_ms: 12.5,
      provider: "deepseek",
      model: "deepseek-chat",
      endpoint: "https://api.deepseek.com",
      message: "连接成功",
    });
  });

  afterEach(() => {
    message.destroy();
    cleanup();
    vi.clearAllMocks();
    vi.unstubAllGlobals();
  });

  it("读取后端有效值且永不把旧 API Key 回填输入框", async () => {
    render(<SettingsPage />);

    expect(await screen.findByRole("heading", { name: "设置" })).toBeVisible();
    expect(screen.getByDisplayValue("deepseek-chat")).toBeInTheDocument();
    const keyInput = screen.getByLabelText("API Key");
    expect(keyInput).toHaveValue("");
    expect(keyInput).toHaveAttribute("placeholder", expect.stringContaining("2468"));
    expect(document.body.textContent).not.toContain("runtime-secret-2468");
    expect(screen.getByText("revision 3")).toBeInTheDocument();
    expect(screen.getAllByText("设备已验证")).toHaveLength(2);
    expect(screen.getByRole("button", { name: /保存并应用/ })).toBeDisabled();
  });

  it("只 PATCH 修改字段并用输入的草稿密钥执行替换", async () => {
    const next = {
      ...settings,
      revision: 4,
      llm: { ...settings.llm, model: "new-model" },
    };
    mocks.updateSettings.mockResolvedValue(
      response(next as SettingsReadResponse),
    );
    render(<SettingsPage />);

    fireEvent.change(await screen.findByLabelText("模型名"), {
      target: { value: "new-model" },
    });
    fireEvent.change(screen.getByLabelText("API Key"), {
      target: { value: "draft-secret-9999" },
    });
    fireEvent.click(screen.getByRole("button", { name: /保存并应用/ }));

    await waitFor(() => expect(mocks.updateSettings).toHaveBeenCalledTimes(1));
    expect(mocks.updateSettings).toHaveBeenCalledWith({
      expected_revision: 3,
      llm: {
        model: "new-model",
        api_key_update: { action: "replace", value: "draft-secret-9999" },
      },
    });
    await screen.findByText(/设置已应用到 revision 4/);
    expect(screen.getByLabelText("API Key")).toHaveValue("");
  });

  it("字段关系非法时不发送保存请求", async () => {
    render(<SettingsPage />);
    const finalTopK = await screen.findByLabelText("最终 Top K");
    fireEvent.change(finalTopK, { target: { value: "20" } });
    fireEvent.blur(finalTopK);
    fireEvent.click(screen.getByRole("button", { name: /保存并应用/ }));

    expect(await screen.findAllByText("不能小于最终 Top K")).not.toHaveLength(0);
    expect(mocks.updateSettings).not.toHaveBeenCalled();
  });

  it("409 冲突时保留草稿并提供重新加载入口", async () => {
    mocks.updateSettings.mockRejectedValue(
      new ApiError(409, "设置已被其他页面更新", "settings_revision_conflict"),
    );
    render(<SettingsPage />);

    fireEvent.change(await screen.findByLabelText("模型名"), {
      target: { value: "conflicting-model" },
    });
    fireEvent.click(screen.getByRole("button", { name: /保存并应用/ }));

    expect(
      await screen.findByText("配置已被其他页面更新", { selector: ".ant-alert-title" }),
    ).toBeInTheDocument();
    expect(screen.getByDisplayValue("conflicting-model")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新加载" })).toBeEnabled();
  });

  it("连接测试不保存草稿，恢复默认使用当前 revision", async () => {
    render(<SettingsPage />);
    fireEvent.change(await screen.findByLabelText("API Key"), {
      target: { value: "draft-test-key" },
    });
    fireEvent.click(screen.getByRole("button", { name: /测试连接/ }));
    await waitFor(() => expect(mocks.testLlmConnection).toHaveBeenCalledTimes(1));
    expect(mocks.testLlmConnection).toHaveBeenCalledWith(
      expect.objectContaining({ api_key: "draft-test-key" }),
    );
    expect(mocks.updateSettings).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /恢复部署默认/ }));
    fireEvent.click(await screen.findByRole("button", { name: "确认恢复" }));
    await waitFor(() => expect(mocks.resetSettings).toHaveBeenCalledWith(3, "all"));
  });
});
