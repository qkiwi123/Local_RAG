import { cleanup, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TagTreeSelect } from "../components/TagTreeSelect";

describe("TagTreeSelect 共享标签选择器", () => {
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

  it("为不同业务上下文提供可辨识名称和统一样式入口", () => {
    render(
      <TagTreeSelect
        tags={[
          {
            tag_path: "课程",
            name: "课程",
            direct_document_count: 1,
            document_count: 2,
          },
          {
            tag_path: "课程/数据结构",
            name: "数据结构",
            direct_document_count: 1,
            document_count: 1,
          },
        ]}
        value={[]}
        onChange={() => undefined}
        ariaLabel="测试标签范围"
      />,
    );

    expect(screen.getByRole("combobox", { name: "测试标签范围" })).toBeVisible();
    expect(document.querySelector(".tag-tree-picker")).toBeInTheDocument();
  });
});
