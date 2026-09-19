import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Citation, CitationPreviewPayload } from "../api/types";

const mocks = vi.hoisted(() => ({
  getCitationPreview: vi.fn(),
  scrollTo: vi.fn(),
  finishOpening: () => {},
}));

// jsdom 不运行 CSS 动画，显式控制打开完成事件以覆盖真实弹窗的时序。
vi.mock("antd", async (importOriginal) => {
  const actual = await importOriginal<typeof import("antd")>();
  return {
    ...actual,
    Modal: (props: React.ComponentProps<typeof actual.Modal>) => {
      mocks.finishOpening = () => props.afterOpenChange?.(true);
      return <actual.Modal {...props} afterOpenChange={undefined} />;
    },
  };
});

vi.mock("../api/client", () => ({
  api: {
    getCitationPreview: mocks.getCitationPreview,
    ocrImageUrl: (documentId: string, imageRef: string) =>
      `/api/documents/id/${documentId}/ocr-images/${imageRef}`,
  },
}));

import { CitationList } from "../components/CitationList";
import { centerCitationTarget } from "../components/citationScroll";

const citation: Citation = {
  source_id: 1,
  chunk_id: "target-unit_s4_e8",
  document_name: "实验2.pdf",
  document_id: "doc-1",
  location_label: "第 7 页",
  heading_path: [],
  excerpt: "目标片段",
  preview_available: true,
};

const preview: CitationPreviewPayload = {
  document_id: "doc-1",
  chunk_id: "target-unit_s4_e8",
  document_name: "实验2.pdf",
  parser_version: "structured-v2",
  location: {
    kind: "page",
    unit_index: 1,
    location_label: "第 7 页",
    page_number: 7,
    heading_path: [],
  },
  target_unit_id: "target-unit",
  highlight_start: 4,
  highlight_end: 8,
  units: [
    {
      unit_id: "first-unit",
      location: {
        kind: "page",
        unit_index: 0,
        location_label: "第 1 页",
        page_number: 1,
        heading_path: [],
      },
      canonical_text: "第一页内容",
      blocks: [
        {
          block_id: "first-block",
          block_type: "paragraph",
          text: "第一页内容",
          sequence: 0,
          attributes: { ocr: true, ocr_image_ref: "image-1" },
          start_char: 0,
          end_char: 5,
        },
      ],
    },
    {
      unit_id: "target-unit",
      location: {
        kind: "page",
        unit_index: 1,
        location_label: "第 7 页",
        page_number: 7,
        heading_path: [],
      },
      canonical_text: "前置文字目标片段后置文字",
      blocks: [
        {
          block_id: "target-block",
          block_type: "paragraph",
          text: "前置文字目标片段后置文字",
          sequence: 0,
          attributes: { ocr: true, ocr_image_ref: "image-2" },
          start_char: 0,
          end_char: 12,
        },
      ],
    },
  ],
};

function rect(top: number, height: number): DOMRect {
  return {
    x: 0,
    y: top,
    top,
    left: 0,
    right: 100,
    bottom: top + height,
    width: 100,
    height,
    toJSON: () => ({}),
  } as DOMRect;
}

beforeEach(() => {
  mocks.getCitationPreview.mockReset();
  mocks.scrollTo.mockReset();
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
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) =>
    window.setTimeout(() => callback(0), 0),
  );
  vi.stubGlobal("cancelAnimationFrame", (handle: number) =>
    window.clearTimeout(handle),
  );
  Object.defineProperty(HTMLElement.prototype, "scrollTo", {
    configurable: true,
    value: mocks.scrollTo,
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("CitationList 来源分组", () => {
  it("同一文档的已引用与未引用片段分别展示，原编号与预览目标保持一致", async () => {
    mocks.getCitationPreview.mockResolvedValue(preview);
    render(<CitationList citations={[
      { ...citation, source_id: 2, chunk_id: "other-chunk", cited_in_answer: true },
      { ...citation, source_id: 4, cited_in_answer: false },
    ]} />);
    expect(screen.getByRole("region", { name: "本轮检索来源" })).toBeVisible();
    const cited = within(screen.getByRole("region", { name: "正文已引用" }));
    const uncited = within(screen.getByRole("region", { name: "正文未引用" }));
    expect(cited.getByText(/【来源2】《实验2.pdf》/)).toBeVisible();
    expect(cited.queryByText(/【来源4】/)).toBeNull();
    expect(uncited.getByText(/【来源4】《实验2.pdf》/)).toBeVisible();
    fireEvent.click(uncited.getByRole("button", { name: /查看原文/ }));
    await screen.findByLabelText("引用原文内容");
    expect(mocks.getCitationPreview).toHaveBeenCalledWith("doc-1", citation.chunk_id);
    const mark = screen.getByLabelText("引用原文内容").querySelector("mark");
    expect(mark).toHaveTextContent("目标片段");
    act(() => mocks.finishOpening());
    await waitFor(() => expect(mocks.scrollTo).toHaveBeenCalled());
  });

  it("拒答或正文无编号时仍展示所有未引用来源", () => {
    render(<CitationList citations={[{ ...citation, cited_in_answer: false }]} />);
    expect(within(screen.getByRole("region", { name: "正文已引用" })).getByText("无")).toBeVisible();
    expect(within(screen.getByRole("region", { name: "正文未引用" })).getByText(/【来源1】/)).toBeVisible();
  });

  it("旧消息缺省归入已引用，空检索不凭空补来源", () => {
    const { rerender } = render(<CitationList citations={[citation]} />);
    expect(within(screen.getByRole("region", { name: "正文已引用" })).getByText(/【来源1】/)).toBeVisible();
    rerender(<CitationList citations={[]} />);
    expect(screen.queryByRole("region", { name: "本轮检索来源" })).toBeNull();
  });
});

describe("CitationList 引用定位", () => {
  it("按容器坐标把精确高亮滚动到视口中央", () => {
    const container = document.createElement("div");
    const section = document.createElement("section");
    const mark = document.createElement("mark");
    section.dataset.citationPreviewTarget = "true";
    section.appendChild(mark);
    container.appendChild(section);
    Object.defineProperty(container, "clientHeight", { value: 400 });
    Object.defineProperty(container, "scrollTop", { value: 200, writable: true });
    vi.spyOn(container, "getBoundingClientRect").mockReturnValue(rect(100, 400));
    vi.spyOn(mark, "getBoundingClientRect").mockReturnValue(rect(900, 20));

    expect(centerCitationTarget(container)).toBe(true);
    expect(mocks.scrollTo).toHaveBeenCalledWith({
      top: 810,
      behavior: "auto",
    });
  });

  it("长段高亮定位到首行，不跳过引用开头", () => {
    const container = document.createElement("div");
    container.innerHTML = '<section data-citation-preview-target="true"><mark>多行高亮</mark></section>';
    const mark = container.querySelector("mark")!;
    Object.defineProperty(container, "clientHeight", { value: 400 });
    vi.spyOn(container, "getBoundingClientRect").mockReturnValue(rect(100, 400));
    vi.spyOn(mark, "getBoundingClientRect").mockReturnValue(rect(900, 1000));
    vi.spyOn(mark, "getClientRects").mockReturnValue([rect(900, 20), rect(920, 20)] as unknown as DOMRectList);

    centerCitationTarget(container);
    expect(mocks.scrollTo).toHaveBeenCalledWith({ top: 610, behavior: "auto" });
  });

  it.each(["table", "section"])("超过一屏的 %s 命中定位顶部", (kind) => {
    const container = document.createElement("div");
    const section = document.createElement("section");
    section.dataset.citationPreviewTarget = "true";
    container.appendChild(section);
    const target = kind === "table" ? document.createElement("table") : section;
    if (kind === "table") {
      target.className = "preview-table-hit";
      section.appendChild(target);
    }
    Object.defineProperty(container, "clientHeight", { value: 400 });
    vi.spyOn(container, "getBoundingClientRect").mockReturnValue(rect(100, 400));
    vi.spyOn(target, "getBoundingClientRect").mockReturnValue(rect(900, 1200));
    centerCitationTarget(container);
    expect(mocks.scrollTo).toHaveBeenCalledWith({ top: 776, behavior: "auto" });
  });

  it("加载完整预览后定位目标，并在用户操作后停止图片加载校正", async () => {
    mocks.getCitationPreview.mockResolvedValue(preview);
    render(<CitationList citations={[citation]} />);

    fireEvent.click(screen.getByRole("button", { name: /查看原文/ }));

    const container = await screen.findByLabelText("引用原文内容");
    act(() => mocks.finishOpening());
    await waitFor(() => expect(mocks.scrollTo).toHaveBeenCalled());
    expect(
      container.querySelector('[data-citation-preview-target="true"] mark'),
    ).toHaveTextContent("目标片段");

    const images = screen.getAllByRole("img", { name: "OCR 识别原图" });
    const positionedCalls = mocks.scrollTo.mock.calls.length;
    fireEvent.load(images[0]);
    expect(mocks.scrollTo.mock.calls.length).toBe(positionedCalls + 1);

    fireEvent.wheel(container);
    fireEvent.load(images[1]);
    expect(mocks.scrollTo.mock.calls.length).toBe(positionedCalls + 1);
  });

  it("快速响应时等待弹窗打开动画完成，不能只等待两帧", async () => {
    mocks.getCitationPreview.mockResolvedValue(preview);
    render(<CitationList citations={[citation]} />);
    fireEvent.click(screen.getByRole("button", { name: /查看原文/ }));
    await screen.findByLabelText("引用原文内容");
    await act(async () => {
      await new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      );
    });
    fireEvent.load(screen.getAllByRole("img", { name: "OCR 识别原图" })[0]);
    expect(mocks.scrollTo).not.toHaveBeenCalled();

    act(() => mocks.finishOpening());
    await waitFor(() => expect(mocks.scrollTo).toHaveBeenCalled());
  });

  it("弹窗先打开、接口后返回时也能定位", async () => {
    let resolvePreview!: (value: CitationPreviewPayload) => void;
    mocks.getCitationPreview.mockReturnValue(new Promise<CitationPreviewPayload>((resolve) => {
      resolvePreview = resolve;
    }));
    render(<CitationList citations={[citation]} />);
    fireEvent.click(screen.getByRole("button", { name: /查看原文/ }));
    act(() => mocks.finishOpening());
    expect(mocks.scrollTo).not.toHaveBeenCalled();
    await act(async () => resolvePreview(preview));
    await waitFor(() => expect(mocks.scrollTo).toHaveBeenCalled());
  });

  it("打开动画结束前已手动浏览时，不再强制跳转", async () => {
    mocks.getCitationPreview.mockResolvedValue(preview);
    render(<CitationList citations={[citation]} />);
    fireEvent.click(screen.getByRole("button", { name: /查看原文/ }));
    const container = await screen.findByLabelText("引用原文内容");
    fireEvent.wheel(container);
    act(() => mocks.finishOpening());
    await act(async () => {
      await new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      );
    });
    fireEvent.load(screen.getAllByRole("img", { name: "OCR 识别原图" })[0]);
    expect(mocks.scrollTo).not.toHaveBeenCalled();
  });
});
