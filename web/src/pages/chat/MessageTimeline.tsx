import { useEffect, useRef } from "react";
import { Alert, Button, Spin, Tag, Typography } from "antd";
import type { ConversationMessage } from "../../api/types";
import { CitationList } from "../../components/CitationList";
import { ReasoningPanel } from "./ReasoningPanel";

const { Text } = Typography;

interface MessageTimelineProps {
  messages: ConversationMessage[];
  pendingQuestion: string | null;
  streaming: boolean;
  streamingText: string;
  streamingReasoning?: string;
  streamingRequestId?: string;
  streamingError?: string | null;
  onRetry: (message: ConversationMessage) => void;
  onOpenTrace: (message: ConversationMessage) => void;
}

export function MessageTimeline({
  messages,
  pendingQuestion,
  streaming,
  streamingText,
  streamingReasoning = "",
  streamingRequestId = "streaming",
  streamingError = null,
  onRetry,
  onOpenTrace,
}: MessageTimelineProps) {
  const timelineRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const node = timelineRef.current;
    if (node) {
      node.scrollTop = node.scrollHeight;
    }
  }, [messages, pendingQuestion, streaming, streamingText, streamingReasoning]);

  const rows = messages.map((item) => ({ item, live: false, pending: false }));
  // 请求完成后以相同 request_id 的历史消息替换临时消息，保留折叠面板状态。
  const hasSavedAnswer = messages.some((item) =>
    item.role === "assistant" && item.request_id === streamingRequestId,
  );
  if (pendingQuestion && !messages.some((item) =>
    item.role === "user" && item.request_id === streamingRequestId,
  )) {
    rows.push({
      item: { message_id: "pending", request_id: streamingRequestId, sequence: 0,
        role: "user", content: pendingQuestion, status: "pending", citations: [], created_at: "" },
      live: false, pending: true,
    });
  }
  if ((streaming || streamingError) && !hasSavedAnswer) {
    rows.push({
      item: { message_id: "streaming", request_id: streamingRequestId, sequence: 0,
        role: "assistant", content: streamingText, reasoning_content: streamingReasoning,
        status: streamingError ? "failed" : "pending", citations: [], created_at: "" },
      live: true, pending: false,
    });
  }

  return (
    <div
      ref={timelineRef}
      className="chat-message-list"
      aria-label="消息记录"
      aria-live="polite"
    >
      {rows.length === 0 ? (
        <Alert
          type="info"
          title="欢迎使用企业知识库问答系统"
          description="先选择或新建会话，再在输入框上方确认检索范围。选择标签可以缩小知识库范围。"
          className="chat-empty-state"
        />
      ) : (
        rows.map(({ item, live, pending }) => (
          <div
            key={`${item.request_id}-${item.role}`}
            aria-label={pending ? "正在发送的问题" : undefined}
            className={`chat-message-row chat-message-row--${item.role}`}
          >
            <div
              className={`chat-message-bubble chat-message-bubble--${item.role}${
                item.status === "failed" ? " chat-message-bubble--failed" : ""
              }${
                pending ? " chat-message-bubble--pending" : live ? " chat-message-bubble--streaming" : ""
              }`}
            >
              {item.role === "assistant" && (
                <ReasoningPanel
                  text={item.reasoning_content ?? ""}
                  answering={Boolean(item.content.trim())}
                  active={live && streaming && !streamingError}
                  failed={item.status === "failed"}
                />
              )}
              {item.content.trim() ? (
                <Text className="chat-message-content">{item.content}</Text>
              ) : item.role === "assistant" && item.status === "complete" ? (
                <Text type="warning" className="chat-message-content">
                  模型未返回可见正文，请重新提问。
                </Text>
              ) : null}
              {live && streaming && !streamingError && (
                <span className="chat-thinking">
                  <Spin size="small" />
                  {item.content.trim() ? " 正在生成回答…" : item.reasoning_content?.trim() ? " 模型正在思考…" : " 正在检索并生成回答…"}
                </span>
              )}
              {live && streamingError && (
                <Alert type="error" title={streamingError} showIcon />
              )}
              {item.status === "failed" && (
                <div className="chat-message-status">
                  <Tag color="red">处理失败</Tag>
                  {item.role === "user" && (
                    <Button size="small" onClick={() => onRetry(item)}>
                      重试该消息
                    </Button>
                  )}
                </div>
              )}
              {item.role === "assistant" && item.citations.length > 0 && (
                <CitationList citations={item.citations} />
              )}
              {item.role === "assistant" && item.trace && (
                <Button
                  size="small"
                  type="link"
                  className="chat-trace-button"
                  onClick={() => onOpenTrace(item)}
                >
                  检索详情
                </Button>
              )}
            </div>
          </div>
        ))
      )}
    </div>
  );
}
