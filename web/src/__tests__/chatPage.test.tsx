import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ChatPage from "../pages/chat/ChatPage";

const mocks = vi.hoisted(() => ({
  listConversations: vi.fn(),
  createConversation: vi.fn(),
  getConversation: vi.fn(),
  renameConversation: vi.fn(),
  deleteConversation: vi.fn(),
  listTags: vi.fn(),
  streamChat: vi.fn(),
}));

vi.mock("../api/client", () => ({
  api: {
    listConversations: mocks.listConversations,
    createConversation: mocks.createConversation,
    getConversation: mocks.getConversation,
    renameConversation: mocks.renameConversation,
    deleteConversation: mocks.deleteConversation,
    listTags: mocks.listTags,
  },
}));

vi.mock("../api/stream", () => ({
  streamChat: mocks.streamChat,
}));

const conversation = {
  conversation_id: "conversation-1",
  title: "测试会话",
  created_at: "2026-08-15T10:00:00",
  updated_at: "2026-08-15T10:00:00",
};

describe("ChatPage 检索范围", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
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
    vi.stubGlobal("crypto", { randomUUID: () => "request-1" });

    mocks.listTags.mockResolvedValue([
      {
        tag_path: "pit",
        name: "pit",
        direct_document_count: 2,
        document_count: 2,
      },
    ]);
    mocks.listConversations.mockResolvedValue([conversation]);
    mocks.getConversation.mockResolvedValue({ ...conversation, messages: [] });
    mocks.streamChat.mockImplementation(
      async (
        _payload: unknown,
        handlers: { onDone?: (event: { event: "done" }) => void },
      ) => {
        handlers.onDone?.({ event: "done" });
        return { events: [], aborted: false };
      },
    );
  });

  afterEach(() => {
    cleanup();
    window.sessionStorage.clear();
    window.localStorage.clear();
    vi.clearAllMocks();
    vi.unstubAllGlobals();
  });

  it("未选择标签时输入区直接显示全部资料", async () => {
    render(<ChatPage />);

    await waitFor(() => expect(mocks.getConversation).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "选择检索资料，当前全部资料" })).toBeVisible();
  });

  it("把当前选中的标签作为下一次聊天请求的检索范围", async () => {
    render(<ChatPage />);

    await waitFor(() => expect(mocks.getConversation).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "选择检索资料，当前全部资料" }));
    fireEvent.click(await screen.findByRole("button", { name: "pit，2 份文档" }));
    expect(screen.queryByRole("button", { name: "完成" })).not.toBeInTheDocument();
    fireEvent.keyDown(screen.getByLabelText("搜索检索范围标签"), { key: "Escape" });
    fireEvent.change(screen.getByPlaceholderText("请输入您的问题..."), {
      target: { value: "第三轮手动测试反馈了什么？" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发\s*送/ }));

    await waitFor(() => expect(mocks.streamChat).toHaveBeenCalledTimes(1));
    expect(mocks.streamChat.mock.calls[0][0]).toMatchObject({
      conversation_id: "conversation-1",
      question: "第三轮手动测试反馈了什么？",
      tags: ["pit"],
    });
  });

  it("空白对话在输入草稿时保持欢迎布局，空白问题不触发布局或请求", async () => {
    render(<ChatPage />);
    await waitFor(() => expect(mocks.getConversation).toHaveBeenCalled());
    const input = screen.getByLabelText("问题输入框");
    fireEvent.change(input, { target: { value: "还没写完的问题" } });
    expect(input.closest(".chat-main")).toHaveClass("chat-main--welcome");
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter", charCode: 13 });
    expect(input.closest(".chat-main")).toHaveClass("chat-main--welcome");
    expect(mocks.streamChat).not.toHaveBeenCalled();
  });

  it("打开已有消息的会话直接使用底部输入布局", async () => {
    mocks.getConversation.mockResolvedValueOnce({ ...conversation, messages: [
      { message_id: "old-u", request_id: "old-r", sequence: 0, role: "user", content: "以前的问题", status: "complete", citations: [], created_at: "" },
    ] });
    render(<ChatPage />);
    expect(await screen.findByText("以前的问题")).toBeVisible();
    expect(screen.getByLabelText("问题输入框").closest(".chat-main")).toHaveClass("chat-main--conversation");
    expect(screen.queryByText("今天想从知识库里了解什么？")).not.toBeInTheDocument();
  });

  it("请求尚未返回时立即显示用户问题，再显示模型思考状态", async () => {
    let finishStream: (() => void) | undefined;
    mocks.streamChat.mockImplementationOnce(
      async (
        _payload: unknown,
        handlers: { onDone?: (event: { event: "done" }) => void },
      ) => {
        await new Promise<void>((resolve) => {
          finishStream = resolve;
        });
        handlers.onDone?.({ event: "done" });
        return { events: [], aborted: false };
      },
    );
    render(<ChatPage />);

    await waitFor(() => expect(mocks.getConversation).toHaveBeenCalledTimes(1));
    const input = screen.getByLabelText("问题输入框");
    expect(input.closest(".chat-main")).toHaveClass("chat-main--welcome");
    fireEvent.change(screen.getByLabelText("问题输入框"), {
      target: { value: "这条问题应该立即出现" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发\s*送/ }));

    expect(await screen.findByText("这条问题应该立即出现")).toBeVisible();
    expect(screen.getByText("正在检索并生成回答…")).toBeVisible();
    expect(input.closest(".chat-main")).toHaveClass("chat-main--conversation");
    expect(screen.getByLabelText("问题输入框")).toBe(input);
    expect(screen.queryByText("今天想从知识库里了解什么？")).not.toBeInTheDocument();
    expect(mocks.getConversation).toHaveBeenCalledTimes(1);

    await act(async () => finishStream?.());
    await waitFor(() => expect(mocks.getConversation).toHaveBeenCalledTimes(2));
  });

  it("检索范围成为输入框内部工具，桌面会话栏仍可调宽", async () => {
    render(<ChatPage />);

    await waitFor(() => expect(mocks.getConversation).toHaveBeenCalled());
    const timeline = screen.getByLabelText("消息记录");
    const scope = screen.getByRole("button", { name: "选择检索资料，当前全部资料" });
    const composer = screen.getByLabelText("问题输入框").closest(".chat-composer");

    expect(timeline.compareDocumentPosition(scope) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(composer).toContainElement(scope);
    const separator = screen.getByRole("separator", {
      name: "调整会话栏宽度",
    });
    expect(separator).toBeVisible();

    fireEvent.mouseDown(separator, { button: 0, clientX: 300 });
    fireEvent.mouseMove(window, { clientX: 348 });
    fireEvent.mouseUp(window);
    await waitFor(() => expect(separator).toHaveAttribute("aria-valuenow", "320"));
  });

  it("接收思考事件并在结束刷新会话后仍可展开已保存的思考", async () => {
    let handlers!: import("../api/stream").ChatStreamHandlers;
    let finish!: () => void;
    mocks.streamChat.mockImplementationOnce(async (_payload, callbacks) => {
      handlers = callbacks;
      await new Promise<void>((resolve) => { finish = resolve; });
      handlers.onDone?.({ event: "done" });
      return { events: [], aborted: false };
    });
    render(<ChatPage />);
    await waitFor(() => expect(mocks.getConversation).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByLabelText("问题输入框"), { target: { value: "新问题" } });
    fireEvent.click(screen.getByRole("button", { name: /发\s*送/ }));
    await waitFor(() => expect(mocks.streamChat).toHaveBeenCalled());
    act(() => handlers.onReasoningDelta?.({ event: "reasoning_delta", text: "核对证据" }));
    expect(screen.getByText("核对证据")).toBeVisible();
    act(() => handlers.onDelta?.({ event: "delta", text: "回答正文" }));
    fireEvent.click(screen.getByRole("button", { name: /思考内容/ }));
    mocks.getConversation.mockResolvedValue({ ...conversation, messages: [
      { message_id: "u1", request_id: "request-1", sequence: 0, role: "user", content: "新问题", status: "complete", citations: [], created_at: "" },
      { message_id: "a1", request_id: "request-1", sequence: 1, role: "assistant", content: "回答正文", reasoning_content: "核对证据", status: "complete", citations: [], created_at: "" },
    ] });
    await act(async () => finish());
    await waitFor(() => expect(mocks.getConversation).toHaveBeenCalledTimes(2));
    expect(screen.getByText("核对证据")).toBeVisible();
    expect(screen.getAllByText("回答正文")).toHaveLength(1);
  });

  it("清除标签后下一次请求恢复为全库检索", async () => {
    render(<ChatPage />);

    await waitFor(() => expect(mocks.getConversation).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "选择检索资料，当前全部资料" }));
    fireEvent.click(await screen.findByRole("button", { name: "pit，2 份文档" }));
    fireEvent.keyDown(screen.getByLabelText("搜索检索范围标签"), { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "清除检索范围" }));
    fireEvent.change(screen.getByPlaceholderText("请输入您的问题..."), {
      target: { value: "全库问题" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发\s*送/ }));

    await waitFor(() => expect(mocks.streamChat).toHaveBeenCalledTimes(1));
    expect(mocks.streamChat.mock.calls[0][0]).toMatchObject({ tags: [] });
  });

  it("收到 409 协议错误后刷新当前会话且不自动重放问题", async () => {
    mocks.streamChat.mockImplementationOnce(
      async (
        _payload: unknown,
        handlers: {
          onError?: (event: {
            event: "error";
            code: string;
            message: string;
          }) => void;
        },
      ) => {
        handlers.onError?.({
          event: "error",
          code: "conversation_conflict",
          message: "会话末尾已变化",
        });
        return { events: [], aborted: false };
      },
    );
    render(<ChatPage />);

    await waitFor(() => expect(mocks.getConversation).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByLabelText("问题输入框"), {
      target: { value: "冲突问题" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发\s*送/ }));

    await waitFor(() => expect(mocks.getConversation.mock.calls.length).toBeGreaterThan(1));
    expect(mocks.streamChat).toHaveBeenCalledTimes(1);
  });

  it("停止生成会中止当前请求，不保留前端伪完成状态", async () => {
    let capturedSignal: AbortSignal | undefined;
    mocks.streamChat.mockImplementationOnce(
      async (
        _payload: unknown,
        _handlers: unknown,
        signal: AbortSignal,
      ) => {
        capturedSignal = signal;
        await new Promise<void>((resolve) => {
          signal.addEventListener("abort", () => resolve(), { once: true });
        });
        return { events: [], aborted: true };
      },
    );
    render(<ChatPage />);

    await waitFor(() => expect(mocks.getConversation).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText("问题输入框"), {
      target: { value: "需要停止的问题" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发\s*送/ }));
    fireEvent.click(await screen.findByRole("button", { name: /停止/ }));

    expect(capturedSignal?.aborted).toBe(true);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /发\s*送/ })).toBeDisabled(),
    );
  });

  it("失败消息重试复用原 request_id，并设置 retry_failed", async () => {
    const failedMessage = {
      message_id: "message-failed",
      request_id: "request-failed",
      sequence: 1,
      role: "user" as const,
      content: "失败的问题",
      status: "failed" as const,
      citations: [],
      created_at: "2026-08-15T10:01:00",
    };
    mocks.getConversation.mockResolvedValue({
      ...conversation,
      messages: [failedMessage],
    });
    render(<ChatPage />);

    fireEvent.click(await screen.findByRole("button", { name: "重试该消息" }));
    await waitFor(() => expect(mocks.streamChat).toHaveBeenCalledTimes(1));
    expect(mocks.streamChat.mock.calls[0][0]).toMatchObject({
      question: "失败的问题",
      request_id: "request-failed",
      retry_failed: true,
    });
    expect(mocks.streamChat.mock.calls[0][0]).toHaveProperty(
      "expected_last_message_id",
      undefined,
    );
  });
});
