import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DocumentItem, DocumentPreview, TagInfo } from "../api/types";
import KnowledgePage from "../pages/knowledge/KnowledgePage";

const mocks = vi.hoisted(() => ({
  listTags: vi.fn(),
  listDocuments: vi.fn(),
  uploadDocument: vi.fn(),
  createTag: vi.fn(),
  renameTag: vi.fn(),
  deleteTag: vi.fn(),
  setDocumentTags: vi.fn(),
  deleteDocument: vi.fn(),
  getDocumentPreview: vi.fn(),
}));

vi.mock("../api/client", () => ({ api: mocks }));

vi.mock("../components/TagTreeSelect", () => ({
  TagTreeSelect: ({
    value,
    onChange,
    placeholder,
  }: {
    value: string[];
    onChange: (value: string[]) => void;
    placeholder?: string;
  }) => (
    <button
      type="button"
      aria-label={placeholder ?? "选择标签"}
      data-value={value.join("|")}
      onClick={() => onChange(["pit"])}
    >
      {placeholder ?? "选择标签"}
    </button>
  ),
}));

const documentItem: DocumentItem = {
  document_id: "doc-1",
  original_name: "项目说明.pdf",
  stored_name: "doc-1.pdf",
  size_bytes: 2048,
  status: "ready",
  chunk_count: 3,
  created_at: "2026-08-15T10:00:00",
  updated_at: "2026-08-15T10:00:00",
  tags: ["项目/说明"],
};

const preview: DocumentPreview = {
  document_id: "doc-1",
  document_name: "项目说明.pdf",
  tags: ["项目/说明"],
  parser_version: "v2",
  unit_count: 1,
  units: [
    {
      unit_id: "unit-1",
      location: {
        kind: "page",
        unit_index: 0,
        location_label: "第 1 页",
        heading_path: [],
      },
      canonical_text: "项目内容",
      blocks: [
        {
          block_id: "block-1",
          block_type: "paragraph",
          text: "项目内容",
          sequence: 0,
          attributes: {},
          start_char: 0,
          end_char: 4,
        },
      ],
    },
  ],
};

async function openUploadDrawer() {
  fireEvent.click(await screen.findByRole("button", { name: "上传文档" }));
  await screen.findByText("选择文件");
}

function selectFiles(files: File[]) {
  const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]');
  expect(fileInput).not.toBeNull();
  fireEvent.change(fileInput!, { target: { files } });
}

describe("KnowledgePage 关键工作流", () => {
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

    mocks.listTags.mockResolvedValue([]);
    mocks.listDocuments.mockResolvedValue({ documents: [], document_items: [] });
    mocks.createTag.mockImplementation(async (tagPath: string) => ({
      tag_path: tagPath,
      name: tagPath.split("/").pop() ?? tagPath,
      direct_document_count: 0,
      document_count: 0,
    }));
    mocks.renameTag.mockResolvedValue({ status: "ok" });
    mocks.deleteTag.mockResolvedValue({ status: "ok" });
    mocks.setDocumentTags.mockResolvedValue({ status: "ok", tags: [] });
    mocks.deleteDocument.mockResolvedValue({ status: "ok" });
    mocks.getDocumentPreview.mockResolvedValue(preview);
    mocks.uploadDocument.mockImplementation(async (file: File) => {
      if (file.name === "失败文档.pdf") throw new Error("模拟上传失败");
      return {
        message: "ok",
        document_id: "doc-1",
        document_name: file.name,
        chunk_count: 3,
        status: "ready",
        warnings: [],
      };
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    vi.unstubAllGlobals();
  });

  it("默认只展示全宽文档工作区，上传任务按需打开", async () => {
    render(<KnowledgePage />);

    expect(await screen.findByRole("heading", { name: "知识文档" })).toBeVisible();
    expect(screen.getByRole("button", { name: "标签管理" })).toBeVisible();
    expect(screen.queryByText("选择文件")).not.toBeInTheDocument();

    await openUploadDrawer();
    expect(screen.getByText("确认上传队列")).toBeVisible();
    expect(
      document.querySelector(
        ".knowledge-task-drawer.ant-drawer-open .app-drawer-resizer",
      ),
    ).toBeInTheDocument();
  });

  it("窄屏断点使用文档卡片，避免固定操作列挤压文档名称", async () => {
    mocks.listDocuments.mockResolvedValue({
      documents: [documentItem.original_name],
      document_items: [documentItem],
    });
    render(<KnowledgePage />);

    expect(await screen.findByLabelText("文档列表")).toBeVisible();
    expect(document.querySelector(".knowledge-document-card")).toBeInTheDocument();
    expect(document.querySelector(".knowledge-documents__table")).toBeNull();
    expect(screen.getByText("2.0 KB · 3 个片段")).toBeVisible();
  });

  it("选择文件时只暂存，点击确认后才逐个提交", async () => {
    render(<KnowledgePage />);
    await openUploadDrawer();

    const file = new File(["content"], "成功文档.pdf", {
      type: "application/pdf",
    });
    selectFiles([file]);
    expect(mocks.uploadDocument).not.toHaveBeenCalled();

    fireEvent.click(
      await screen.findByRole("button", { name: /确认上传（1 个文件）/ }),
    );
    await waitFor(() => expect(mocks.uploadDocument).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/已入库（3 chunks）/)).toBeVisible();
  });

  it("移除暂存文件不会发起上传请求", async () => {
    render(<KnowledgePage />);
    await openUploadDrawer();
    const file = new File(["draft"], "待移除.pdf", {
      type: "application/pdf",
    });
    selectFiles([file]);

    fireEvent.click(
      await screen.findByRole("button", { name: "移除上传文件 待移除.pdf" }),
    );

    expect(mocks.uploadDocument).not.toHaveBeenCalled();
    expect(screen.queryByText("待移除.pdf")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "请选择文件后确认上传" }),
    ).toBeDisabled();
  });

  it("关闭上传抽屉后仍保留暂存队列", async () => {
    render(<KnowledgePage />);
    await openUploadDrawer();
    selectFiles([
      new File(["draft"], "保留队列.pdf", { type: "application/pdf" }),
    ]);

    const closeButton = document.querySelector<HTMLButtonElement>(
      ".knowledge-task-drawer.ant-drawer-open .ant-drawer-close",
    );
    expect(closeButton).not.toBeNull();
    fireEvent.click(closeButton!);
    await waitFor(() =>
      expect(
        document.querySelector(".knowledge-task-drawer.ant-drawer-open"),
      ).toBeNull(),
    );

    await openUploadDrawer();
    expect(screen.getByText("保留队列.pdf")).toBeVisible();
    expect(
      screen.getByRole("button", { name: /确认上传（1 个文件）/ }),
    ).toBeEnabled();
    expect(mocks.uploadDocument).not.toHaveBeenCalled();
  });

  it("文档标签筛选继续使用服务端 tags 参数", async () => {
    render(<KnowledgePage />);

    fireEvent.click(
      await screen.findByRole("button", { name: "按标签筛选文档" }),
    );
    await waitFor(() =>
      expect(mocks.listDocuments).toHaveBeenLastCalledWith(["pit"]),
    );
  });

  it("失败文件保留，重试不会重复提交成功项", async () => {
    render(<KnowledgePage />);
    await openUploadDrawer();

    const successful = new File(["ok"], "成功文档.pdf", {
      type: "application/pdf",
    });
    const failed = new File(["bad"], "失败文档.pdf", {
      type: "application/pdf",
    });
    selectFiles([successful, failed]);
    fireEvent.click(
      await screen.findByRole("button", { name: /确认上传（2 个文件）/ }),
    );

    expect(await screen.findByText("模拟上传失败")).toBeVisible();
    expect(screen.getByText("失败文件保留，可重试")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "重试失败文件" }));

    await waitFor(() => expect(mocks.uploadDocument).toHaveBeenCalledTimes(3));
    expect(
      (mocks.uploadDocument.mock.calls[2][0] as File).name,
    ).toBe("失败文档.pdf");
  });

  it("OCR 上传警告使用明确的 AI 识别风险提示", async () => {
    mocks.uploadDocument.mockResolvedValueOnce({
      message: "ok",
      document_id: "doc-ocr",
      document_name: "扫描件.pdf",
      chunk_count: 1,
      status: "ready",
      warnings: [
        {
          code: "ocr_low_confidence",
          message: "部分文字置信度较低",
          location_label: "第 2 页",
        },
      ],
    });
    render(<KnowledgePage />);
    await openUploadDrawer();
    selectFiles([new File(["scan"], "扫描件.pdf", { type: "application/pdf" })]);
    fireEvent.click(
      await screen.findByRole("button", { name: /确认上传（1 个文件）/ }),
    );

    expect(await screen.findByText("AI 识别，可能存在误差")).toBeVisible();
    expect(screen.getByText("第 2 页：部分文字置信度较低")).toBeVisible();
  });

  it("上传时新建标签会保留已有选择并自动选中新标签", async () => {
    render(<KnowledgePage />);
    await openUploadDrawer();

    const selector = screen.getByRole("button", {
      name: "选择已有标签（可选，可多选）",
    });
    fireEvent.click(selector);
    fireEvent.change(screen.getByPlaceholderText("新标签路径（如：课程/数据结构）"), {
      target: { value: "项目/新标签" },
    });
    fireEvent.click(screen.getByRole("button", { name: "新建并选中" }));

    await waitFor(() =>
      expect(selector).toHaveAttribute("data-value", "pit|项目/新标签"),
    );
  });

  it("标签抽屉按层级展示标签树及双层计数", async () => {
    const tagItems: TagInfo[] = [
      {
        tag_path: "课程",
        name: "课程",
        direct_document_count: 1,
        document_count: 3,
      },
      {
        tag_path: "课程/数据结构",
        name: "数据结构",
        direct_document_count: 2,
        document_count: 2,
      },
    ];
    mocks.listTags.mockResolvedValue(tagItems);
    render(<KnowledgePage />);

    fireEvent.click(await screen.findByRole("button", { name: "标签管理" }));
    expect(await screen.findByRole("tree", { name: "标签层级" })).toBeVisible();
    expect(
      document.querySelector(
        ".knowledge-task-drawer.ant-drawer-open .app-drawer-resizer",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("本层 1 · 含子级 3")).toBeVisible();
    expect(screen.getByText("2 文档")).toBeVisible();
  });

  it("文档预览、标签编辑与删除仍调用原有接口", async () => {
    mocks.listDocuments.mockResolvedValue({
      documents: [documentItem.original_name],
      document_items: [documentItem],
    });
    render(<KnowledgePage />);

    fireEvent.click(
      await screen.findByRole("button", { name: "预览文档 项目说明.pdf" }),
    );
    expect(await screen.findByText("项目内容")).toBeVisible();
    expect(mocks.getDocumentPreview).toHaveBeenCalledWith("doc-1");
    expect(
      document.querySelector(
        ".knowledge-preview-drawer.ant-drawer-open .app-drawer-resizer",
      ),
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "编辑文档标签 项目说明.pdf" }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "选择已有标签" }));
    fireEvent.click(screen.getByRole("button", { name: "保存标签" }));
    await waitFor(() =>
      expect(mocks.setDocumentTags).toHaveBeenCalledWith("doc-1", ["pit"]),
    );

    fireEvent.click(screen.getByRole("button", { name: "删除文档 项目说明.pdf" }));
    fireEvent.click(await screen.findByRole("button", { name: "确定删除" }));
    await waitFor(() => expect(mocks.deleteDocument).toHaveBeenCalledWith("doc-1"));
  });
});
