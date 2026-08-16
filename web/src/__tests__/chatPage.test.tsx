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

vi.mock("../components/TagTreeSelect", () => ({
  TagTreeSelect: ({
    value,
    onChange,
  }: {
    value: string[];
    onChange: (value: string[]) => void;
  }) => (
    <div aria-label="检索范围选择器">
      <button type="button" onClick={() => onChange(["pit"])}>
        选择 pit
      </button>
      <button type="button" onClick={() => onChange([])} disabled={!value.length}>
        清除标签
      </button>
    </div>
  ),
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

  it("未选择标签时明确显示全部知识库，而不是只依赖选择器占位文案", async () => {
    render(<ChatPage />);

    await waitFor(() => expect(mocks.getConversation).toHaveBeenCalled());
    expect(screen.getByText("全部知识库")).toBeVisible();
  });

  it("把当前选中的标签作为下一次聊天请求的检索范围", async () => {
    render(<ChatPage />);

    await waitFor(() => expect(mocks.getConversation).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "选择 pit" }));
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
    fireEvent.change(screen.getByLabelText("问题输入框"), {
      target: { value: "这条问题应该立即出现" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发\s*送/ }));

    expect(await screen.findByText("这条问题应该立即出现")).toBeVisible();
    expect(screen.getByText("正在检索并生成回答…")).toBeVisible();
    expect(mocks.getConversation).toHaveBeenCalledTimes(1);

    await act(async () => finishStream?.());
    await waitFor(() => expect(mocks.getConversation).toHaveBeenCalledTimes(2));
  });

  it("检索范围紧邻输入框上方，桌面会话栏提供宽度调节边界", async () => {
    render(<ChatPage />);

    await waitFor(() => expect(mocks.getConversation).toHaveBeenCalled());
    const timeline = screen.getByLabelText("消息记录");
    const scope = screen.getByRole("region", { name: "检索范围" });
    const composer = screen.getByLabelText("问题输入框").closest(".chat-composer");

    expect(timeline.compareDocumentPosition(scope) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(scope.compareDocumentPosition(composer!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const separator = screen.getByRole("separator", {
      name: "调整会话栏宽度",
    });
    expect(separator).toBeVisible();

    fireEvent.mouseDown(separator, { button: 0, clientX: 300 });
    fireEvent.mouseMove(window, { clientX: 348 });
    fireEvent.mouseUp(window);
    await waitFor(() => expect(separator).toHaveAttribute("aria-valuenow", "320"));
  });

  it("清除标签后下一次请求恢复为全库检索", async () => {
    render(<ChatPage />);

    await waitFor(() => expect(mocks.getConversation).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "选择 pit" }));
    fireEvent.click(screen.getByRole("button", { name: "清除标签" }));
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
