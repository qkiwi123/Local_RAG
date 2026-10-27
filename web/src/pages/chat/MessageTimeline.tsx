import { useEffect, useRef } from "react";
import { Alert, Button, Spin, Tag, Typography } from "antd";
import { EditOutlined, ReloadOutlined } from "@ant-design/icons";
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
  replacingMessageId?: string;
  pendingTags?: string[];
  onEdit?: (message: ConversationMessage) => void;
  supportsMessageEdit?: boolean;
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
  replacingMessageId,
  pendingTags = [],
  onEdit,
  supportsMessageEdit = true,
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

  const target = messages.find((item) => item.message_id === replacingMessageId);
  // 请求完成后以相同 request_id 的历史消息替换临时消息，保留折叠面板状态。
  const hasSavedAnswer = messages.some((item) =>
    item.role === "assistant" && item.request_id === streamingRequestId,
  );
  const replacing = Boolean(target && streaming && !hasSavedAnswer);
  const replacementFailed = Boolean(target && !streaming && streamingError && !hasSavedAnswer);
  const visibleMessages = replacing ? messages.filter((item) => item.sequence < target!.sequence) : messages;
  const rows = visibleMessages.map((item) => ({ item, live: false, pending: false }));
  if (pendingQuestion && !replacementFailed && !visibleMessages.some((item) =>
    item.role === "user" && item.request_id === streamingRequestId,
  )) {
    rows.push({
      item: { message_id: "pending", request_id: streamingRequestId, sequence: 0,
        role: "user", content: pendingQuestion, status: "pending", retrieval_tags: pendingTags, citations: [], created_at: "" },
      live: false, pending: true,
    });
  }
  if ((streaming || streamingError) && !hasSavedAnswer && !replacementFailed) {
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
      <div className="chat-content-column">
      {rows.length === 0 ? (
        <section className="chat-empty-state">
          <span className="chat-empty-state__eyebrow">让资料成为线索</span>
          <h2>今天想从知识库里了解什么？</h2>
          <p>提出问题，找到答案，也找到出处。</p>
        </section>
      ) : (
        rows.map(({ item, live, pending }) => (
          <div
            key={`${item.request_id}-${item.role}`}
            aria-label={pending ? "正在发送的问题" : undefined}
            className={`chat-message-row chat-message-row--${item.role}`}
          >
            <div
              className={item.role === "assistant" ? "chat-answer" : `chat-message-bubble chat-message-bubble--user${
                item.status === "failed" ? " chat-message-bubble--failed" : ""
              }${pending ? " chat-message-bubble--pending" : ""}`}
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
            {item.role === "user" && <div className="chat-message-tools">
              <span className="chat-message-scope" title={item.retrieval_tags?.join("、") || undefined}>
                {item.retrieval_tags == null ? "检索范围未记录" : item.retrieval_tags.length
                  ? `检索范围：${item.retrieval_tags.join("、")}` : "检索范围：全部资料"}
              </span>
              {!pending && item.status !== "pending" && <span className="chat-message-tools__actions">
                {onEdit && <Button type="text" size="small" icon={<EditOutlined />}
                  aria-label={`编辑问题：${item.content}`} onClick={() => onEdit(item)} disabled={streaming || !supportsMessageEdit}>编辑</Button>}
                <Button type="text" size="small" icon={<ReloadOutlined />} disabled={streaming || !supportsMessageEdit}
                  aria-label={`重试问题：${item.content}`} onClick={() => onRetry(item)}>
                  {item.status === "failed" ? "重试该消息" : "重试"}</Button>
              </span>}
            </div>}
          </div>
        ))
      )}
      {replacing && <p className="chat-regenerate-notice" role="status">正在重新生成，成功后替换记录；失败或停止时原记录保留。</p>}
      {replacementFailed && <Alert className="chat-regenerate-notice" type="warning" showIcon
        title="重新生成未完成，原记录已保留。" description={streamingError} />}
      {!supportsMessageEdit && messages.length > 0 && <Alert type="info" className="chat-regenerate-notice"
        title="请重启更新后的后端服务，以启用消息编辑、重试及范围保存。" />}
      </div>
    </div>
  );
}
