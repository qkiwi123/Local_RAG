import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RetrievalTrace } from "../api/types";
import { TraceDrawer } from "../components/TraceDrawer";

const trace: RetrievalTrace = {
  retrieval_mode: "dense",
  reranker_enabled: true,
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
    total: 60,
  },
  tag_filter: ["课程"],
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
    expect(screen.getByText("20.0 ms")).toBeVisible();
    expect(screen.getByText("其他阶段（追问改写/历史/引用提取/持久化等）")).toBeVisible();
    expect(document.querySelectorAll(".trace-timing-row")).toHaveLength(4);
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
});
