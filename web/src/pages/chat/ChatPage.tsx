import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Col,
  Dropdown,
  Input,
  List,
  Modal,
  Row,
  Spin,
  Tag,
  Typography,
  message,
} from "antd";
import {
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  StopOutlined,
} from "@ant-design/icons";
import { api } from "../../api/client";
import { streamChat } from "../../api/stream";
import type {
  ConversationMessage,
  ConversationSummary,
  TagInfo,
} from "../../api/types";
import { CitationList } from "../../components/CitationList";
import { TagTreeSelect } from "../../components/TagTreeSelect";
import { TraceDrawer } from "../../components/TraceDrawer";

const { Text } = Typography;

const RESYNC_CODES = ["conversation_conflict", "request_in_progress", "stale_retry"];

export default function ChatPage() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [tags, setTags] = useState<TagInfo[]>([]);
  const [tagFilter, setTagFilter] = useState<string[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [streamingText, setStreamingText] = useState("");
  const [traceOpen, setTraceOpen] = useState<ConversationMessage | null>(null);
  const [renameTarget, setRenameTarget] = useState<ConversationSummary | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const loadedRef = useRef(false);

  const refreshConversations = useCallback(async () => {
    try {
      setConversations(await api.listConversations());
    } catch {
      // 后端不可达时保持现状
    }
  }, []);

  const loadConversation = useCallback(async (id: string) => {
    try {
      const detail = await api.getConversation(id);
      setCurrentId(id);
      setMessages(detail.messages);
    } catch (error) {
      message.error(`加载会话失败: ${(error as Error).message}`);
    }
  }, []);

  const refreshTags = useCallback(async () => {
    try {
      setTags(await api.listTags());
    } catch {
      // 忽略
    }
  }, []);

  useEffect(() => {
    if (loadedRef.current) return;
    loadedRef.current = true;
    (async () => {
      await refreshTags();
      const list = await api.listConversations().catch(() => []);
      setConversations(list);
      if (list.length) {
        await loadConversation(list[0].conversation_id);
      }
    })();
  }, [loadConversation, refreshTags]);

  const createConversation = async () => {
    try {
      const created = await api.createConversation();
      await refreshConversations();
      await loadConversation(created.conversation_id);
    } catch (error) {
      message.error(`新建对话失败: ${(error as Error).message}`);
    }
  };

  const deleteConversation = async (id: string) => {
    try {
      await api.deleteConversation(id);
      if (currentId === id) {
        setCurrentId(null);
        setMessages([]);
      }
      await refreshConversations();
      message.success("会话已删除");
    } catch (error) {
      message.error(`删除失败: ${(error as Error).message}`);
    }
  };

  const saveRename = async () => {
    if (!renameTarget) return;
    try {
      await api.renameConversation(renameTarget.conversation_id, renameValue.trim());
      setRenameTarget(null);
      await refreshConversations();
      message.success("标题已更新");
    } catch (error) {
      message.error(`重命名失败: ${(error as Error).message}`);
    }
  };

  const lastCompleteId = () => {
    const complete = messages.filter((m) => m.status === "complete");
    return complete.length ? complete[complete.length - 1].message_id : null;
  };

  const resyncCurrent = async () => {
    if (currentId) {
      await loadConversation(currentId);
    }
    await refreshConversations();
  };

  const send = async (question: string, retryMessage?: ConversationMessage) => {
    let conversationId = currentId;
    if (!conversationId) {
      try {
        const created = await api.createConversation();
        setCurrentId(created.conversation_id);
        conversationId = created.conversation_id;
        await refreshConversations();
      } catch (error) {
        message.error(`无法创建会话: ${(error as Error).message}`);
        return;
      }
    }

    const controller = new AbortController();
    abortRef.current = controller;
    setStreaming(true);
    setStreamingText("");

    try {
      await streamChat(
        {
          question,
          conversation_id: conversationId,
          request_id: retryMessage ? retryMessage.request_id : crypto.randomUUID(),
          expected_last_message_id: retryMessage ? undefined : lastCompleteId(),
          retry_failed: Boolean(retryMessage),
          tags: tagFilter,
        },
        {
          onDelta: (event) => {
            setStreamingText((previous) => previous + event.text);
          },
          onDone: () => {
            setStreaming(false);
          },
          onError: async (event) => {
            setStreaming(false);
            if (RESYNC_CODES.includes(event.code)) {
              await resyncCurrent();
              message.warning("会话已在服务端更新，已自动刷新；请重新发送问题。");
            } else {
              message.error(event.message || "回答失败");
            }
            if (currentId) {
              await loadConversation(currentId);
            }
          },
        },
        controller.signal,
      );
    } catch (error) {
      const err = error as { status?: number; code?: string; message?: string };
      setStreaming(false);
      if (err.code && RESYNC_CODES.includes(err.code)) {
        await resyncCurrent();
        message.warning("会话已在服务端更新，已自动刷新；请重新发送问题。");
      } else if (err.status === 404) {
        message.error("会话不存在，请新建会话后重试");
        setCurrentId(null);
        setMessages([]);
      } else {
        message.error(err.message || "问答失败，请重试");
      }
    } finally {
      abortRef.current = null;
      if (conversationId) {
        await loadConversation(conversationId);
      }
    }
  };

  const stopStreaming = () => {
    abortRef.current?.abort();
    setStreaming(false);
    message.info("已停止生成（本次回答按失败处理，可重试）");
  };

  const submitInput = () => {
    const question = input.trim();
    if (!question || streaming) return;
    setInput("");
    void send(question);
  };

  return (
    <Row gutter={16}>
      <Col span={6}>
        <div
          style={{
            background: "#fff",
            borderRadius: 8,
            padding: 12,
            height: "calc(100vh - 32px)",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={createConversation}
            block
          >
            新建对话
          </Button>
          <List
            style={{ flex: 1, overflowY: "auto", marginTop: 12 }}
            dataSource={conversations}
            renderItem={(item) => (
              <List.Item
                style={{
                  cursor: "pointer",
                  background: item.conversation_id === currentId ? "#e6f4ff" : undefined,
                  borderRadius: 6,
                  padding: "8px 12px",
                }}
                onClick={() => void loadConversation(item.conversation_id)}
                actions={[
                  <Dropdown
                    key="actions"
                    menu={{
                      items: [
                        {
                          key: "rename",
                          icon: <EditOutlined />,
                          label: "重命名",
                          onClick: () => {
                            setRenameTarget(item);
                            setRenameValue(item.title);
                          },
                        },
                        {
                          key: "delete",
                          icon: <DeleteOutlined />,
                          danger: true,
                          label: "删除",
                          onClick: () => void deleteConversation(item.conversation_id),
                        },
                      ],
                    }}
                  >
                    <Button size="small" type="text" onClick={(e) => e.stopPropagation()}>
                      ⋯
                    </Button>
                  </Dropdown>,
                ]}
              >
                <Text ellipsis style={{ maxWidth: 150 }}>
                  {item.conversation_id === currentId ? "▶ " : ""}
                  {item.title}
                </Text>
              </List.Item>
            )}
          />
        </div>
      </Col>

      <Col span={18}>
        <div
          style={{
            background: "#fff",
            borderRadius: 8,
            padding: 16,
            height: "calc(100vh - 32px)",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <Row align="middle" style={{ marginBottom: 12 }}>
            <Col flex="auto">
              <TagTreeSelect
                tags={tags}
                value={tagFilter}
                onChange={setTagFilter}
              />
            </Col>
            <Col>
              {tagFilter.length > 0 && (
                <Tag closable onClose={() => setTagFilter([])} style={{ marginLeft: 8 }}>
                  筛选：{tagFilter.join("、")}
                </Tag>
              )}
            </Col>
          </Row>

          <div style={{ flex: 1, overflowY: "auto", paddingBottom: 12 }}>
            {messages.length === 0 && !streaming ? (
              <Alert
                type="info"
                message="👋 欢迎使用企业知识库问答系统"
                description="先在左侧选择或新建会话；可先在「知识库管理」上传文档并打标签，然后用标签缩小检索范围。"
              />
            ) : (
              messages.map((item) => (
                <div
                  key={item.message_id}
                  style={{
                    display: "flex",
                    justifyContent: item.role === "user" ? "flex-end" : "flex-start",
                    marginBottom: 12,
                  }}
                >
                  <div
                    style={{
                      maxWidth: "86%",
                      background: item.role === "user" ? "#e6f4ff" : "#fafafa",
                      borderRadius: 8,
                      padding: "8px 12px",
                    }}
                  >
                    <Text style={{ whiteSpace: "pre-wrap" }}>{item.content}</Text>
                    {item.status === "failed" && (
                      <div style={{ marginTop: 4 }}>
                        <Tag color="red">处理失败</Tag>
                        {item.role === "user" && (
                          <Button
                            size="small"
                            onClick={() => void send(item.content, item)}
                          >
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
                        onClick={() => setTraceOpen(item)}
                      >
                        🔎 检索详情
                      </Button>
                    )}
                  </div>
                </div>
              ))
            )}
            {streaming && (
              <div style={{ display: "flex", justifyContent: "flex-start", marginBottom: 12 }}>
                <div style={{ maxWidth: "86%", background: "#fafafa", borderRadius: 8, padding: "8px 12px" }}>
                  {streamingText ? (
                    <Text style={{ whiteSpace: "pre-wrap" }}>
                      {streamingText}
                      <Spin size="small" style={{ marginLeft: 8 }} />
                    </Text>
                  ) : (
                    <Spin size="small" /> 
                  )}
                </div>
              </div>
            )}
          </div>

          <Row gutter={8} align="middle">
            <Col flex="auto">
              <Input.TextArea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="请输入您的问题..."
                autoSize={{ minRows: 1, maxRows: 6 }}
                onPressEnter={(e) => {
                  if (!e.shiftKey) {
                    e.preventDefault();
                    submitInput();
                  }
                }}
              />
            </Col>
            <Col>
              {streaming ? (
                <Button danger icon={<StopOutlined />} onClick={stopStreaming}>
                  停止
                </Button>
              ) : (
                <Button type="primary" onClick={submitInput} disabled={!input.trim()}>
                  发送
                </Button>
              )}
            </Col>
          </Row>
        </div>
      </Col>

      <Modal
        open={Boolean(renameTarget)}
        title="重命名会话"
        onOk={saveRename}
        onCancel={() => setRenameTarget(null)}
      >
        <Input
          value={renameValue}
          onChange={(e) => setRenameValue(e.target.value)}
          maxLength={120}
        />
      </Modal>

      {traceOpen?.trace && (
        <TraceDrawer trace={traceOpen.trace} onClose={() => setTraceOpen(null)} />
      )}
    </Row>
  );
}
