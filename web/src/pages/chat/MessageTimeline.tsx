import { useEffect, useRef } from "react";
import { Alert, Button, Spin, Tag, Typography } from "antd";
import type { ConversationMessage } from "../../api/types";
import { CitationList } from "../../components/CitationList";

const { Text } = Typography;

interface MessageTimelineProps {
  messages: ConversationMessage[];
  pendingQuestion: string | null;
  streaming: boolean;
  streamingText: string;
  onRetry: (message: ConversationMessage) => void;
  onOpenTrace: (message: ConversationMessage) => void;
}

export function MessageTimeline({
  messages,
  pendingQuestion,
  streaming,
  streamingText,
  onRetry,
  onOpenTrace,
}: MessageTimelineProps) {
  const timelineRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const node = timelineRef.current;
    if (node) {
      node.scrollTop = node.scrollHeight;
    }
  }, [messages, pendingQuestion, streaming, streamingText]);

  return (
    <div
      ref={timelineRef}
      className="chat-message-list"
      aria-label="消息记录"
      aria-live="polite"
    >
      {messages.length === 0 && !pendingQuestion && !streaming ? (
        <Alert
          type="info"
          title="欢迎使用企业知识库问答系统"
          description="先选择或新建会话，再在输入框上方确认检索范围。选择标签可以缩小知识库范围。"
          className="chat-empty-state"
        />
      ) : (
        messages.map((item) => (
          <div
            key={item.message_id}
            className={`chat-message-row chat-message-row--${item.role}`}
          >
            <div
              className={`chat-message-bubble chat-message-bubble--${item.role}${
                item.status === "failed" ? " chat-message-bubble--failed" : ""
              }`}
            >
              <Text className="chat-message-content">{item.content}</Text>
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

      {pendingQuestion && (
        <div
          className="chat-message-row chat-message-row--user"
          aria-label="正在发送的问题"
        >
          <div className="chat-message-bubble chat-message-bubble--user chat-message-bubble--pending">
            <Text className="chat-message-content">{pendingQuestion}</Text>
          </div>
        </div>
      )}

      {streaming && (
        <div className="chat-message-row chat-message-row--assistant">
          <div className="chat-message-bubble chat-message-bubble--assistant chat-message-bubble--streaming">
            {streamingText ? (
              <Text className="chat-message-content">
                {streamingText}
                <Spin size="small" className="chat-streaming-spinner" />
              </Text>
            ) : (
              <span className="chat-thinking">
                <Spin size="small" /> 正在检索并生成回答…
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
