import { useCallback, useEffect, useRef, useState } from "react";
import { message } from "antd";
import { api } from "../../api/client";
import { streamChat } from "../../api/stream";
import type {
  ConversationMessage,
  ConversationSummary,
  TagInfo,
} from "../../api/types";

const RESYNC_CODES = ["conversation_conflict", "request_in_progress", "stale_retry"];
const ACTIVE_CONVERSATION_KEY = "rag.activeConversationId";

interface PendingQuestion {
  conversationId: string | null;
  content: string;
}

function readStoredConversationId(): string | null {
  try {
    return window.sessionStorage.getItem(ACTIVE_CONVERSATION_KEY);
  } catch {
    return null;
  }
}

function storeConversationId(id: string | null) {
  try {
    if (id) {
      window.sessionStorage.setItem(ACTIVE_CONVERSATION_KEY, id);
    } else {
      window.sessionStorage.removeItem(ACTIVE_CONVERSATION_KEY);
    }
  } catch {
    // 无存储权限时仍可在当前页面正常使用。
  }
}

export function useChatController() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [tags, setTags] = useState<TagInfo[]>([]);
  const [tagFilter, setTagFilter] = useState<string[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [streamingText, setStreamingText] = useState("");
  const [streamingReasoning, setStreamingReasoning] = useState("");
  const [streamingRequestId, setStreamingRequestId] = useState("");
  const [streamingError, setStreamingError] = useState<string | null>(null);
  const [pendingQuestion, setPendingQuestion] = useState<PendingQuestion | null>(
    null,
  );
  const abortRef = useRef<AbortController | null>(null);
  const currentIdRef = useRef<string | null>(null);
  const loadSequenceRef = useRef(0);
  const loadedRef = useRef(false);

  const setActiveConversation = useCallback((id: string | null) => {
    if (currentIdRef.current !== id) {
      setStreamingError(null);
      setStreamingReasoning("");
      setStreamingText("");
    }
    currentIdRef.current = id;
    setCurrentId(id);
    storeConversationId(id);
  }, []);

  const refreshConversations = useCallback(async () => {
    try {
      const list = await api.listConversations();
      setConversations(list);
      return list;
    } catch {
      return null;
    }
  }, []);

  const loadConversation = useCallback(
    async (id: string) => {
      const sequence = ++loadSequenceRef.current;
      setActiveConversation(id);
      try {
        const detail = await api.getConversation(id);
        if (sequence !== loadSequenceRef.current || currentIdRef.current !== id) {
          return;
        }
        setMessages(detail.messages);
      } catch (error) {
        if (sequence === loadSequenceRef.current) {
          message.error(`加载会话失败: ${(error as Error).message}`);
        }
      }
    },
    [setActiveConversation],
  );

  const refreshTags = useCallback(async () => {
    try {
      setTags(await api.listTags());
    } catch {
      // 标签加载失败不阻断会话读取。
    }
  }, []);

  useEffect(() => {
    if (loadedRef.current) return;
    loadedRef.current = true;
    void (async () => {
      await refreshTags();
      const list = (await refreshConversations()) ?? [];
      const storedId = readStoredConversationId();
      const target = list.find((item) => item.conversation_id === storedId) ?? list[0];
      if (target) {
        await loadConversation(target.conversation_id);
      }
    })();
  }, [loadConversation, refreshConversations, refreshTags]);

  const createConversation = useCallback(async () => {
    try {
      const created = await api.createConversation();
      await refreshConversations();
      await loadConversation(created.conversation_id);
    } catch (error) {
      message.error(`新建对话失败: ${(error as Error).message}`);
    }
  }, [loadConversation, refreshConversations]);

  const deleteConversation = useCallback(
    async (id: string) => {
      try {
        await api.deleteConversation(id);
        const list = (await refreshConversations()) ?? [];
        if (currentIdRef.current === id) {
          setMessages([]);
          const next = list[0];
          if (next) {
            await loadConversation(next.conversation_id);
          } else {
            setActiveConversation(null);
          }
        }
        message.success("会话已删除");
      } catch (error) {
        message.error(`删除失败: ${(error as Error).message}`);
      }
    },
    [loadConversation, refreshConversations, setActiveConversation],
  );

  const renameConversation = useCallback(
    async (id: string, title: string) => {
      try {
        await api.renameConversation(id, title.trim());
        await refreshConversations();
        message.success("标题已更新");
        return true;
      } catch (error) {
        message.error(`重命名失败: ${(error as Error).message}`);
        return false;
      }
    },
    [refreshConversations],
  );

  const lastCompleteId = useCallback(() => {
    const complete = messages.filter((item) => item.status === "complete");
    return complete.length ? complete[complete.length - 1].message_id : null;
  }, [messages]);

  const resyncConversation = useCallback(
    async (id: string) => {
      if (currentIdRef.current === id) {
        await loadConversation(id);
      }
      await refreshConversations();
    },
    [loadConversation, refreshConversations],
  );

  const send = useCallback(
    async (question: string, retryMessage?: ConversationMessage) => {
      let conversationId = currentIdRef.current;
      if (!retryMessage) {
        setPendingQuestion({ conversationId, content: question });
      }
      if (!conversationId) {
        try {
          const created = await api.createConversation();
          conversationId = created.conversation_id;
          setActiveConversation(conversationId);
          if (!retryMessage) {
            setPendingQuestion({ conversationId, content: question });
          }
          await refreshConversations();
        } catch (error) {
          setPendingQuestion(null);
          message.error(`无法创建会话: ${(error as Error).message}`);
          return;
        }
      }

      const controller = new AbortController();
      const activeTags = [...tagFilter];
      let protocolError: { code: string; message: string } | null = null;
      let receivedDone = false;
      abortRef.current = controller;
      const requestId = retryMessage ? retryMessage.request_id : crypto.randomUUID();
      setStreamingRequestId(requestId);
      setStreaming(true);
      setStreamingText("");
      setStreamingReasoning("");
      setStreamingError(null);

      try {
        const result = await streamChat(
          {
            question,
            conversation_id: conversationId,
            request_id: requestId,
            expected_last_message_id: retryMessage ? undefined : lastCompleteId(),
            retry_failed: Boolean(retryMessage),
            tags: activeTags,
          },
          {
            onDelta: (event) => {
              if (currentIdRef.current === conversationId) {
                setStreamingText((previous) => previous + event.text);
              }
            },
            onReasoningDelta: (event) => {
              if (currentIdRef.current === conversationId) {
                setStreamingReasoning((previous) => previous + event.text);
              }
            },
            onDone: () => { receivedDone = true; },
            onError: (event) => {
              protocolError = { code: event.code, message: event.message };
              if (currentIdRef.current === conversationId) setStreamingError(event.message);
            },
          },
          controller.signal,
        );

        if (result.aborted && currentIdRef.current === conversationId) {
          setStreamingError("已停止生成，本次内容未保存，可重试。");
        } else if (!receivedDone && !protocolError && currentIdRef.current === conversationId) {
          setStreamingError("连接已结束，但未收到回答完成确认，请检查会话或重试。");
        }

        if (protocolError) {
          const failure = protocolError as { code: string; message: string };
          if (RESYNC_CODES.includes(failure.code)) {
            await resyncConversation(conversationId);
            message.warning("会话已在服务端更新，已自动刷新；请重新发送问题。");
          } else {
            message.error(failure.message || "回答失败");
          }
        }
      } catch (error) {
        const failure = error as {
          status?: number;
          code?: string;
          message?: string;
        };
        if (currentIdRef.current === conversationId) {
          setStreamingError(failure.message || "问答失败，请重试");
        }
        if (failure.code && RESYNC_CODES.includes(failure.code)) {
          await resyncConversation(conversationId);
          message.warning("会话已在服务端更新，已自动刷新；请重新发送问题。");
        } else if (failure.status === 404) {
          message.error("会话不存在，请新建会话后重试");
          setMessages([]);
          setActiveConversation(null);
        } else {
          message.error(failure.message || "问答失败，请重试");
        }
      } finally {
        abortRef.current = null;
        if (currentIdRef.current === conversationId) {
          await loadConversation(conversationId);
        }
        setStreaming(false);
        await refreshConversations();
        setPendingQuestion(null);
      }
    },
    [
      lastCompleteId,
      loadConversation,
      refreshConversations,
      resyncConversation,
      setActiveConversation,
      tagFilter,
    ],
  );

  const stopStreaming = useCallback(() => {
    abortRef.current?.abort();
    message.info("已停止生成（本次回答按失败处理，可重试）");
  }, []);

  const submitInput = useCallback(() => {
    const question = input.trim();
    if (!question || streaming) return;
    setInput("");
    void send(question);
  }, [input, send, streaming]);

  return {
    conversations,
    currentId,
    messages,
    tags,
    tagFilter,
    setTagFilter,
    input,
    setInput,
    streaming,
    streamingText,
    streamingReasoning,
    streamingRequestId,
    streamingError,
    pendingQuestion:
      pendingQuestion?.conversationId === currentId
        ? pendingQuestion.content
        : null,
    loadConversation,
    createConversation,
    deleteConversation,
    renameConversation,
    send,
    stopStreaming,
    submitInput,
  };
}
