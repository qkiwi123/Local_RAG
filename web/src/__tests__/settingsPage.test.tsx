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
  testVisionConnection: vi.fn(),
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
    answer_thinking_mode: "provider_default",
    answer_reasoning_effort: "provider_default",
    rewrite_enabled: true,
    rewrite_thinking_mode: "disabled",
    rewrite_reasoning_effort: "provider_default",
    rewrite_temperature: 0,
    rewrite_max_tokens: 200,
    rewrite_timeout_seconds: 30,
  },
  vision: {
    enabled: false, source: "follow_llm", provider: "openai_compatible",
    base_url: "", model: "glm-4.6v-flash",
    api_key: { configured: false, source: "none", masked_suffix: null },
    effective_connection: {
      provider: "deepseek", base_url: "https://api.deepseek.com", model: "deepseek-chat",
      api_key: { configured: true, source: "runtime", masked_suffix: "2468" },
    },
    pdf_scanned_enabled: false, pdf_page_images_enabled: false, embedded_images_enabled: false,
    recognize_thinking_mode: "disabled", recognize_reasoning_effort: "provider_default",
    recognize_temperature: 0, recognize_max_tokens: 4000, recognize_timeout_seconds: 60,
    describe_thinking_mode: "disabled", describe_reasoning_effort: "provider_default",
    describe_temperature: 0, describe_max_tokens: 800, describe_timeout_seconds: 60,
    max_image_edge: 1600, max_calls_per_document: 20,
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
    vision_activation: "next_document_upload",
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
    "llm.answer_thinking_mode": "environment",
    "llm.answer_reasoning_effort": "environment",
    "llm.rewrite_enabled": "environment",
    "llm.rewrite_thinking_mode": "environment",
    "llm.rewrite_reasoning_effort": "environment",
    "llm.rewrite_temperature": "environment",
    "llm.rewrite_max_tokens": "environment",
    "llm.rewrite_timeout_seconds": "environment",
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
    "llm.answer_thinking_mode": "next_request",
    "llm.answer_reasoning_effort": "next_request",
    "llm.rewrite_enabled": "next_request",
    "llm.rewrite_thinking_mode": "next_request",
    "llm.rewrite_reasoning_effort": "next_request",
    "llm.rewrite_temperature": "next_request",
    "llm.rewrite_max_tokens": "next_request",
    "llm.rewrite_timeout_seconds": "next_request",
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
    sparse_backend: "rank_bm25_memory",
    sparse_index_status: "ready",
    sparse_index_directory: "sparse_index",
    sparse_index_generation: null,
    sparse_manifest_hash: null,
    sparse_index_load_ms: 0,
    sparse_contract_version: "nfkc-lower-regex-jieba-exact-v1",
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
    mocks.testVisionConnection.mockResolvedValue({
      success: true, latency_ms: 20, provider: "deepseek", model: "deepseek-chat",
      endpoint: "https://api.deepseek.com", message: "图片识别验证成功",
      error_code: null, request_id: null,
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
    expect(screen.getByLabelText("视觉 API Key")).not.toBeVisible();
    expect(screen.getByLabelText("视觉模型名")).not.toBeVisible();
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

  it("思考开启时仍允许编辑 Temperature，并独立保存改写预算", async () => {
    mocks.getSettings.mockResolvedValue({
      ...settings,
      llm: { ...settings.llm, answer_thinking_mode: "enabled" },
    });
    render(<SettingsPage />);

    const answerTemperature = await screen.findByLabelText("回答 Temperature");
    expect(answerTemperature).toBeEnabled();
    fireEvent.change(answerTemperature, { target: { value: "0.7" } });
    fireEvent.change(screen.getByLabelText("改写最大完成 Tokens"), {
      target: { value: "320" },
    });
    fireEvent.click(screen.getByRole("button", { name: /保存并应用/ }));

    await waitFor(() => expect(mocks.updateSettings).toHaveBeenCalledTimes(1));
    expect(mocks.updateSettings).toHaveBeenCalledWith({
      expected_revision: 3,
      llm: {
        temperature: 0.7,
        rewrite_max_tokens: 320,
      },
    });
  });

  it("独立保存视觉开关、扫描场景和识别预算，不修改问答配置", async () => {
    render(<SettingsPage />);
    fireEvent.click(await screen.findByLabelText("启用视觉处理"));
    fireEvent.click(screen.getByLabelText("处理扫描 PDF"));
    fireEvent.change(screen.getByLabelText("识别最大完成 Tokens"), { target: { value: "6000" } });
    fireEvent.click(screen.getByRole("button", { name: /保存并应用/ }));
    await waitFor(() => expect(mocks.updateSettings).toHaveBeenCalledWith({
      expected_revision: 3,
      vision: { enabled: true, pdf_scanned_enabled: true, recognize_max_tokens: 6000 },
    }));
  });

  it("跟随测试采用已保存问答连接及视觉草稿，不携带问答草稿密钥", async () => {
    render(<SettingsPage />);
    fireEvent.change(await screen.findByLabelText("API Key"), { target: { value: "unsaved-llm-key" } });
    fireEvent.change(screen.getByLabelText("识别最大完成 Tokens"), { target: { value: "5000" } });
    fireEvent.click(screen.getByRole("button", { name: /测试视觉连接/ }));
    await waitFor(() => expect(mocks.testVisionConnection).toHaveBeenCalledTimes(1));
    expect(mocks.testVisionConnection.mock.calls[0][0]).toEqual({
      task: "recognize", vision: {
        source: "follow_llm", max_image_edge: 1600,
        recognize_thinking_mode: "disabled", recognize_reasoning_effort: "provider_default",
        recognize_temperature: 0, recognize_max_tokens: 5000, recognize_timeout_seconds: 60,
      },
    });
    expect(mocks.updateSettings).not.toHaveBeenCalled();
    expect(await screen.findByText(/使用当前已保存的问答模型/)).toBeVisible();
  });

  it("自定义视觉测试携带独立密钥和描述参数，保存后清空密钥草稿", async () => {
    mocks.getSettings.mockResolvedValue({ ...settings, vision: {
      ...settings.vision, source: "custom", base_url: "https://vision.example.com/v1",
    } });
    render(<SettingsPage />);
    fireEvent.change(await screen.findByLabelText("视觉 API Key"), { target: { value: "vision-draft-key" } });
    fireEvent.click(screen.getByRole("radio", { name: "描述" }));
    fireEvent.click(screen.getByRole("button", { name: /测试视觉连接/ }));
    await waitFor(() => expect(mocks.testVisionConnection).toHaveBeenCalledTimes(1));
    expect(mocks.testVisionConnection.mock.calls[0][0]).toMatchObject({
      task: "describe", vision: {
        source: "custom", base_url: "https://vision.example.com/v1",
        describe_max_tokens: 800,
        api_key_update: { action: "replace", value: "vision-draft-key" },
      },
    });
    expect(screen.getByLabelText("API Key")).toHaveValue("");
    fireEvent.click(screen.getByRole("button", { name: /保存并应用/ }));
    await waitFor(() => expect(mocks.updateSettings).toHaveBeenCalledWith({
      expected_revision: 3, vision: { api_key_update: { action: "replace", value: "vision-draft-key" } },
    }));
    await waitFor(() => expect(screen.getByLabelText("视觉 API Key")).toHaveValue(""));
  }, 15000);

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
  }, 15000);
});
