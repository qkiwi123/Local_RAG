import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RetrievalTrace } from "../api/types";
import { TraceDrawer } from "../components/TraceDrawer";

const trace: RetrievalTrace = {
  retrieval_mode: "dense",
  reranker_enabled: true,
  timing_contract_version: "chat-v2",
  followup_rewrite_status: "succeeded",
  settings_revision: 7,
  effective_config: {
    retrieval_mode: "hybrid",
    final_top_k: 5,
    dense_candidate_k: 15,
    dense_distance_threshold: 0.67,
    bm25_top_k: 12,
    bm25_k1: 1.5,
    bm25_b: 0.75,
    rrf_k: 60,
    reranker_enabled: true,
    reranker_candidate_k: 10,
    max_chunks_per_document: 3,
    refusal_gate_enabled: false,
  },
  llm: {
    called: false,
    provider: "deepseek",
    model: "deepseek-v4-flash",
    answer: {
      called: false,
      thinking_mode: "enabled",
      reasoning_effort: "high",
      temperature: 0.3,
      max_tokens: 2000,
      timeout_seconds: 120,
    },
    rewrite: {
      called: true,
      thinking_mode: "disabled",
      reasoning_effort: "provider_default",
      temperature: 0,
      max_tokens: 200,
      timeout_seconds: 30,
    },
  },
  chunks: {
    chunk_1: {
      chunk_id: "chunk_1",
      document_name: "示例文档.md",
      location_label: "第一章",
      content: "用于验证候选明细表格的示例内容。",
    },
  },
  stages: [
    {
      name: "dense",
      candidates: [
        {
          chunk_id: "chunk_1",
          rank: 1,
          distance: 0.12,
          filtered_reason: null,
          selected: true,
        },
      ],
    },
  ],
  selected_chunk_ids: ["chunk_1"],
  context: { chunk_count: 1, char_count: 20, token_count: 12 },
  timings_ms: {
    query_embedding: 20,
    dense_retrieval: 30,
    followup_rewrite: 5,
    citation_extraction: 2,
    persistence: 3,
    time_to_first_delta: 70,
    delta_window: 20,
    total: 100,
  },
  tag_filter: ["课程"],
  sparse_backend: "bm25s_mmap",
  sparse_index_generation: "generation-1",
  sparse_manifest_hash: "abc123",
  sparse_index_status: "ready",
  sparse_fallback_reason: null,
  sparse_index_load_ms: 4.5,
  sparse_contract_version: "contract-v1",
};

describe("TraceDrawer 检索详情", () => {
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
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("将耗时数值和进度条分离，并可展开候选明细", () => {
    render(<TraceDrawer trace={trace} onClose={() => undefined} />);

    expect(screen.getByText("查询向量化")).toBeVisible();
    expect(screen.getByText("revision 7")).toBeVisible();
    expect(screen.getAllByText("未调用").length).toBeGreaterThan(0);
    expect(screen.getByText("deepseek / deepseek-v4-flash")).toBeVisible();
    expect(screen.getByText("思考 enabled")).toBeVisible();
    expect(screen.getByText("思考 disabled")).toBeVisible();
    expect(screen.getByText("· 最多 200 tokens")).toBeVisible();
    expect(screen.getByText("hybrid / 5")).toBeVisible();
    expect(screen.getByText("candidates 15 · distance ≤ 0.67")).toBeVisible();
    expect(screen.getByText("bm25s_mmap")).toBeVisible();
    expect(screen.getByText("generation-1")).toBeVisible();
    expect(screen.getByText("加载 4.5 ms")).toBeVisible();
    expect(screen.getAllByText("20.0 ms")).toHaveLength(2);
    expect(screen.getByText("chat-v2 · 请求入口至持久化前")).toBeVisible();
    expect(screen.getByText("成功")).toBeVisible();
    expect(screen.getAllByText("追问改写")).toHaveLength(2);
    expect(screen.getByText("引用提取")).toBeVisible();
    expect(screen.getByText("总耗时内未拆分 / 调度开销")).toBeVisible();
    expect(screen.getByText("会话提交（不含 Trace 写入）")).toBeVisible();
    expect(screen.getByText("请求入口到首个 Delta")).toBeVisible();
    expect(screen.getByText("首个到最后一个 Delta 窗口")).toBeVisible();
    expect(screen.queryByText(/其他阶段/)).not.toBeInTheDocument();
    expect(
      document.querySelector(
        ".trace-drawer.ant-drawer-open .app-drawer-resizer",
      ),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByText("Dense（1）"));

    expect(screen.getByText("示例文档.md")).toBeInTheDocument();
    expect(screen.getByText("第一章")).toBeInTheDocument();
    expect(document.querySelector(".trace-drawer .ant-table-content")).toBeInTheDocument();
  });

  it("旧 Trace 缺少配置版本时保持可读且明确标注未记录", () => {
    render(
      <TraceDrawer
        trace={{
          ...trace,
          timing_contract_version: null,
          followup_rewrite_status: null,
          settings_revision: null,
          effective_config: null,
          llm: null,
          timings_ms: {
            query_embedding: 20,
            dense_retrieval: 30,
            total: 60,
          },
        }}
        onClose={() => undefined}
      />,
    );

    expect(screen.getAllByText("旧 Trace 未记录").length).toBeGreaterThan(1);
    expect(screen.getByText("dense / 未记录")).toBeVisible();
    expect(screen.getByText("总耗时（旧口径）")).toBeVisible();
    expect(
      screen.getByText("旧 Trace 未记录统一计量边界，不计算或归因阶段差值。"),
    ).toBeVisible();
    expect(screen.queryByText(/其他阶段/)).not.toBeInTheDocument();
  });
});
