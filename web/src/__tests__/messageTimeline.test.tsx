import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ConversationMessage } from "../api/types";
import { MessageTimeline } from "../pages/chat/MessageTimeline";

afterEach(cleanup);

function renderTimeline(messages: ConversationMessage[]) {
  render(
    <MessageTimeline
      messages={messages}
      pendingQuestion={null}
      streaming={false}
      streamingText=""
      onRetry={vi.fn()}
      onOpenTrace={vi.fn()}
    />,
  );
}

describe("MessageTimeline 历史空回答", () => {
  it("不会把已完成的空助手消息显示成空白气泡", () => {
    renderTimeline([
      {
        message_id: "assistant-empty",
        request_id: "request-1",
        sequence: 1,
        role: "assistant",
        content: "",
        status: "complete",
        citations: [],
        trace: {
          retrieval_mode: "dense",
          reranker_enabled: false,
          chunks: {},
          stages: [],
          selected_chunk_ids: [],
          context: { chunk_count: 0, char_count: 0, token_count: 0 },
          timings_ms: {},
          tag_filter: [],
        },
        created_at: "2026-09-17T15:27:08+00:00",
      },
    ]);

    expect(
      screen.getByText("模型未返回可见正文，请重新提问。"),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "检索详情" })).toBeVisible();
  });
});

describe("思考展示与正文切换", () => {
  const props = { messages: [], pendingQuestion: null, streaming: true,
    streamingText: "", streamingReasoning: "正在核对资料", streamingRequestId: "r1",
    onRetry: vi.fn(), onOpenTrace: vi.fn() };

  it("思考默认展开，正文首次到达折叠，手动展开后流式更新和保存不重置", () => {
    const view = render(<MessageTimeline {...props} />);
    expect(screen.getByRole("button", { name: /思考中/ })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("正在核对资料")).toBeVisible();
    view.rerender(<MessageTimeline {...props} streamingText="正式答案" />);
    const toggle = screen.getByRole("button", { name: /思考内容/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    view.rerender(<MessageTimeline {...props} streamingText="正式答案继续输出" />);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const saved: ConversationMessage = { message_id: "saved", request_id: "r1", sequence: 1,
      role: "assistant", content: "正式答案继续输出", reasoning_content: "正在核对资料",
      status: "complete", citations: [], created_at: "" };
    view.rerender(<MessageTimeline {...props} streaming={false} messages={[saved]} />);
    expect(screen.getByText("正在核对资料")).toBeVisible();
    // 离开后重新进入历史会话应默认折叠。
    view.unmount();
    render(<MessageTimeline {...props} streaming={false} messages={[saved]} />);
    expect(screen.getByRole("button", { name: /思考内容/ })).toHaveAttribute("aria-expanded", "false");
  });

  it("没有实际思考文本时不显示空面板", () => {
    render(<MessageTimeline {...props} streamingReasoning="" streamingText="直接回答" />);
    expect(screen.queryByRole("region", { name: "模型思考" })).not.toBeInTheDocument();
  });

  it("只有思考的失败保留当前页内容并标明未完成，重试恢复默认展开", () => {
    const view = render(<MessageTimeline {...props} />);
    fireEvent.click(screen.getByRole("button", { name: /思考中/ }));
    view.rerender(<MessageTimeline {...props} streaming={false} streamingError="模型没有生成最终回答" />);
    expect(screen.getByRole("alert")).toHaveTextContent("模型没有生成最终回答");
    expect(screen.getByRole("button", { name: /生成未完成/ })).toBeVisible();
    view.rerender(<MessageTimeline {...props} />);
    expect(screen.getByRole("button", { name: /思考中/ })).toHaveAttribute("aria-expanded", "true");
  });
});
