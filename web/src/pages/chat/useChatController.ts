import { useCallback, useEffect, useRef, useState } from "react";
import { message } from "antd";
import { api } from "../../api/client";
import { streamChat } from "../../api/stream";
import type { ConversationMessage, ConversationSummary, TagInfo } from "../../api/types";

const RESYNC_CODES = ["conversation_conflict", "request_in_progress", "stale_retry"];
const ACTIVE_CONVERSATION_KEY = "rag.activeConversationId";
const NEW_CONVERSATION = "new";

interface RunState {
  requestId: string;
  question: string;
  streaming: boolean;
  text: string;
  reasoning: string;
  error: string | null;
  replaceMessageId?: string;
  tags: string[];
}

interface ActiveRun {
  key: string;
  controller: AbortController;
  state: RunState;
}

function readStoredConversationId(): string | null {
  try { return window.sessionStorage.getItem(ACTIVE_CONVERSATION_KEY); }
  catch { return null; }
}

function storeConversationId(id: string | null) {
  try { window.sessionStorage.setItem(ACTIVE_CONVERSATION_KEY, id ?? NEW_CONVERSATION); }
  catch { /* 无存储权限时仍可在当前页面正常使用。 */ }
}

export function useChatController() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [viewKey, setViewKey] = useState("draft-0");
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [supportsMessageEdit, setSupportsMessageEdit] = useState(false);
  const [tags, setTags] = useState<TagInfo[]>([]);
  const [tagFilter, setTagFilter] = useState<string[]>([]);
  const [input, setInput] = useState("");
  const [runs, setRuns] = useState<Record<string, RunState>>({});
  const requestsRef = useRef(new Map<string, ActiveRun>());
  const currentIdRef = useRef<string | null>(null);
  const viewKeyRef = useRef("draft-0");
  const draftSequenceRef = useRef(0);
  const loadSequenceRef = useRef(0);
  const listSequenceRef = useRef(0);
  const loadedRef = useRef(false);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    const requests = requestsRef.current;
    return () => {
      mountedRef.current = false;
      for (const run of requests.values()) run.controller.abort();
      requests.clear();
    };
  }, []);

  const publishRun = useCallback((run: ActiveRun, patch: Partial<RunState>) => {
    if (!mountedRef.current || requestsRef.current.get(run.key) !== run) return;
    run.state = { ...run.state, ...patch };
    const key = run.key;
    const state = run.state;
    setRuns((previous) => ({ ...previous, [key]: state }));
  }, []);

  const retireRun = useCallback((run: ActiveRun) => {
    if (!mountedRef.current || requestsRef.current.get(run.key) !== run) return;
    requestsRef.current.delete(run.key);
    const key = run.key;
    setRuns((previous) => {
      if (run.state.error) return { ...previous, [key]: { ...run.state, streaming: false } };
      const next = { ...previous };
      delete next[key];
      return next;
    });
  }, []);

  const setActiveConversation = useCallback((id: string | null) => {
    const key = id ?? `draft-${++draftSequenceRef.current}`;
    currentIdRef.current = id;
    viewKeyRef.current = key;
    setCurrentId(id);
    setViewKey(key);
    storeConversationId(id);
  }, []);

  const refreshConversations = useCallback(async () => {
    const sequence = ++listSequenceRef.current;
    try {
      const list = await api.listConversations();
      if (mountedRef.current && sequence === listSequenceRef.current) setConversations(list);
      return list;
    } catch { return null; }
  }, []);

  const loadConversation = useCallback(async (id: string, preserveView = false) => {
    if (!preserveView && currentIdRef.current === id && requestsRef.current.has(id)) return;
    const sequence = ++loadSequenceRef.current;
    if (!preserveView) {
      setActiveConversation(id);
      setMessages([]);
      setInput("");
      if (!requestsRef.current.has(id)) {
        setRuns((previous) => { const next = { ...previous }; delete next[id]; return next; });
      }
    }
    try {
      const detail = await api.getConversation(id);
      if (mountedRef.current && sequence === loadSequenceRef.current && currentIdRef.current === id) {
        setMessages(detail.messages);
        setSupportsMessageEdit(detail.supports_message_edit === true);
      }
    } catch (error) {
      if (mountedRef.current && sequence === loadSequenceRef.current) {
        message.error(`加载会话失败: ${(error as Error).message}`);
      }
    }
  }, [setActiveConversation]);

  const refreshTags = useCallback(async () => {
    try {
      const list = await api.listTags();
      if (mountedRef.current) setTags(list);
    } catch { /* 标签加载失败不阻断会话读取。 */ }
  }, []);

  useEffect(() => {
    if (loadedRef.current) return;
    loadedRef.current = true;
    void (async () => {
      const sequence = loadSequenceRef.current;
      await refreshTags();
      const list = (await refreshConversations()) ?? [];
      const storedId = readStoredConversationId();
      if (!mountedRef.current || storedId === NEW_CONVERSATION || sequence !== loadSequenceRef.current) return;
      const target = list.find((item) => item.conversation_id === storedId) ?? list[0];
      if (target) await loadConversation(target.conversation_id);
    })();
  }, [loadConversation, refreshConversations, refreshTags]);

  const createConversation = useCallback(() => {
    ++loadSequenceRef.current;
    setActiveConversation(null);
    setMessages([]);
    setInput("");
  }, [setActiveConversation]);

  const deleteConversation = useCallback(async (id: string) => {
    try {
      await api.deleteConversation(id);
      const run = requestsRef.current.get(id);
      if (run) {
        requestsRef.current.delete(id);
        run.controller.abort();
      }
      setRuns((previous) => { const next = { ...previous }; delete next[id]; return next; });
      const list = (await refreshConversations()) ?? [];
      if (currentIdRef.current === id) {
        if (list[0]) await loadConversation(list[0].conversation_id);
        else createConversation();
      }
      message.success("会话已删除");
    } catch (error) { message.error(`删除失败: ${(error as Error).message}`); }
  }, [createConversation, loadConversation, refreshConversations]);

  const renameConversation = useCallback(async (id: string, title: string) => {
    try {
      await api.renameConversation(id, title.trim());
      await refreshConversations();
      message.success("标题已更新");
      return true;
    } catch (error) {
      message.error(`重命名失败: ${(error as Error).message}`);
      return false;
    }
  }, [refreshConversations]);

  const send = useCallback(async (question: string, retryMessage?: ConversationMessage, scopeOverride?: string[], expectedTailId?: string, targetConversationId?: string) => {
    const startingKey = viewKeyRef.current;
    if (!question.trim() || requestsRef.current.has(startingKey)) return;
    if (retryMessage && !supportsMessageEdit) {
      message.warning("请先重启更新后的后端服务，再编辑或重试消息。");
      return;
    }
    if (retryMessage && (!currentIdRef.current || (targetConversationId && currentIdRef.current !== targetConversationId))) return;
    let conversationId = currentIdRef.current;
    const complete = messages.filter((item) => item.status === "complete");
    const expectedLastId = retryMessage ? (expectedTailId ?? messages.at(-1)?.message_id)
      : conversationId && complete.length ? complete[complete.length - 1].message_id : null;
    const activeTags = [...(scopeOverride ?? tagFilter)];
    const run: ActiveRun = {
      key: startingKey,
      controller: new AbortController(),
      state: { requestId: crypto.randomUUID(), question, tags: activeTags,
        replaceMessageId: retryMessage?.message_id,
        streaming: true, text: "", reasoning: "", error: null },
    };
    requestsRef.current.set(startingKey, run);
    ++loadSequenceRef.current;
    publishRun(run, {});
    const isOwned = () => mountedRef.current && requestsRef.current.get(run.key) === run;
    const isVisible = () => isOwned() && viewKeyRef.current === run.key;
    let receivedDone = false;
    let protocolError: { code: string; message: string } | null = null;

    try {
      if (!conversationId) {
        const created = await api.createConversation();
        if (!isOwned()) return;
        conversationId = created.conversation_id;
        const draftKey = run.key;
        requestsRef.current.delete(draftKey);
        run.key = conversationId;
        requestsRef.current.set(run.key, run);
        setRuns((previous) => {
          const next = { ...previous };
          delete next[draftKey];
          next[run.key] = run.state;
          return next;
        });
        // 创建结果属于原来的空白页；用户已切走时只加入列表，不抢回视图。
        if (viewKeyRef.current === draftKey) setActiveConversation(conversationId);
        setConversations((previous) => [created, ...previous.filter((item) => item.conversation_id !== conversationId)]);
        void refreshConversations();
      }
      if (!isOwned()) return;
      if (run.controller.signal.aborted) {
        publishRun(run, { error: "已停止生成，本次内容未保存，可重试。" });
        return;
      }
      const result = await streamChat({ question, conversation_id: conversationId,
        request_id: run.state.requestId, expected_last_message_id: expectedLastId,
        replace_message_id: retryMessage?.message_id,
        retry_failed: false, tags: activeTags }, {
        onDelta: (event) => publishRun(run, { text: run.state.text + event.text }),
        onReasoningDelta: (event) => publishRun(run, { reasoning: run.state.reasoning + event.text }),
        onDone: () => { receivedDone = true; },
        onError: (event) => {
          protocolError = { code: event.code, message: event.message };
          publishRun(run, { error: event.message });
        },
      }, run.controller.signal);
      if (!isOwned()) return;
      if (result.aborted) publishRun(run, { error: "已停止生成，本次内容未保存，可重试。" });
      else if (!receivedDone && !protocolError) {
        publishRun(run, { error: "连接已结束，但未收到回答完成确认，请检查会话或重试。" });
      }
      if (protocolError && isVisible()) {
        const failure = protocolError as { code: string; message: string };
        if (RESYNC_CODES.includes(failure.code)) {
          message.warning("会话已在服务端更新，已自动刷新；请重新发送问题。");
        } else message.error(failure.message || "回答失败");
      }
    } catch (error) {
      if (!isOwned()) return;
      const failure = error as { status?: number; code?: string; message?: string };
      publishRun(run, { error: failure.message || "问答失败，请重试" });
      if (isVisible()) {
        if (!conversationId) setInput(question);
        if (failure.code && RESYNC_CODES.includes(failure.code)) {
          message.warning("会话已在服务端更新，已自动刷新；请重新发送问题。");
        } else if (failure.status === 404) {
          message.error("会话不存在，请新建会话后重试");
          createConversation();
        } else message.error(failure.message || "问答失败，请重试");
      }
    } finally {
      // 导航只切换可见会话，不取消网络请求；后台完成后按原会话正常落库。
      if (isVisible() && conversationId) await loadConversation(conversationId, true);
      retireRun(run);
      if (mountedRef.current) await refreshConversations();
    }
  }, [createConversation, loadConversation, messages, publishRun, refreshConversations, retireRun, setActiveConversation, tagFilter, supportsMessageEdit]);

  const stopStreaming = useCallback(() => {
    const run = requestsRef.current.get(viewKeyRef.current);
    if (!run) return;
    run.controller.abort();
    message.info("已停止生成（本次回答按失败处理，可重试）");
  }, []);

  const submitInput = useCallback(() => {
    const question = input.trim();
    if (!question || requestsRef.current.has(viewKeyRef.current)) return;
    setInput("");
    void send(question);
  }, [input, send]);

  const activeRun = runs[viewKey];
  return {
    conversations, currentId, messages, tags, tagFilter, setTagFilter, input, setInput, supportsMessageEdit,
    streaming: activeRun?.streaming ?? false,
    streamingText: activeRun?.text ?? "",
    streamingReasoning: activeRun?.reasoning ?? "",
    streamingRequestId: activeRun?.requestId ?? "",
    streamingError: activeRun?.error ?? null,
    replacingMessageId: activeRun?.replaceMessageId,
    pendingTags: activeRun?.tags ?? [],
    pendingQuestion: activeRun?.question ?? null,
    loadConversation, createConversation, deleteConversation, renameConversation, send, stopStreaming, submitInput,
  };
}
