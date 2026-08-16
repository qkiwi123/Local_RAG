import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
} from "react";
import { Button, Drawer, Input, Modal } from "antd";
import { MenuOutlined } from "@ant-design/icons";
import type {
  ConversationMessage,
  ConversationSummary,
} from "../../api/types";
import { TraceDrawer } from "../../components/TraceDrawer";
import { usePanelResize } from "../../components/usePanelResize";
import { ChatComposer } from "./ChatComposer";
import { ConversationRail } from "./ConversationRail";
import { MessageTimeline } from "./MessageTimeline";
import { RetrievalScopeBar } from "./RetrievalScopeBar";
import { useChatController } from "./useChatController";
import "./chat-page.css";

function useNarrowChatLayout() {
  const [narrow, setNarrow] = useState(() =>
    window.matchMedia("(max-width: 820px)").matches,
  );

  useEffect(() => {
    const media = window.matchMedia("(max-width: 820px)");
    const update = () => setNarrow(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  return narrow;
}

export default function ChatPage() {
  const chat = useChatController();
  const narrow = useNarrowChatLayout();
  const getMaxSidebarWidth = useCallback(
    () => Math.min(440, Math.max(220, window.innerWidth - 520)),
    [],
  );
  const sidebarResize = usePanelResize({
    defaultSize: 272,
    minSize: 220,
    getMaxSize: getMaxSidebarWidth,
    storageKey: "rag.chat.sidebar.width",
    edge: "right",
    ariaLabel: "调整会话栏宽度",
  });
  const [conversationDrawerOpen, setConversationDrawerOpen] = useState(false);
  const [traceOpen, setTraceOpen] = useState<ConversationMessage | null>(null);
  const [renameTarget, setRenameTarget] = useState<ConversationSummary | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const currentConversation = useMemo(
    () =>
      chat.conversations.find(
        (item) => item.conversation_id === chat.currentId,
      ) ?? null,
    [chat.conversations, chat.currentId],
  );

  useEffect(() => {
    if (!narrow) {
      setConversationDrawerOpen(false);
    }
  }, [narrow]);

  const startRename = (conversation: ConversationSummary) => {
    setRenameTarget(conversation);
    setRenameValue(conversation.title);
  };

  const saveRename = async () => {
    if (!renameTarget || !renameValue.trim()) return;
    const saved = await chat.renameConversation(
      renameTarget.conversation_id,
      renameValue,
    );
    if (saved) {
      setRenameTarget(null);
    }
  };

  const rail = (
    <ConversationRail
      conversations={chat.conversations}
      currentId={chat.currentId}
      onCreate={() => void chat.createConversation()}
      onSelect={(id) => void chat.loadConversation(id)}
      onDelete={(id) => void chat.deleteConversation(id)}
      onRename={startRename}
      onNavigate={() => setConversationDrawerOpen(false)}
    />
  );

  return (
    <div
      className="chat-page"
      style={
        {
          "--chat-sidebar-width": `${sidebarResize.size}px`,
        } as CSSProperties
      }
    >
      {!narrow && <aside className="chat-sidebar">{rail}</aside>}
      {!narrow && (
        <div
          className="chat-layout-resizer"
          {...sidebarResize.separatorProps}
        >
          <span className="chat-layout-resizer__line" aria-hidden="true" />
        </div>
      )}

      <main className="chat-main">
        <header className="chat-context-bar">
          {narrow && (
            <Button
              icon={<MenuOutlined />}
              onClick={() => setConversationDrawerOpen(true)}
              aria-label="打开会话列表"
            >
              会话
            </Button>
          )}
          <div className="chat-context-bar__copy">
            <span className="chat-context-bar__eyebrow">当前会话</span>
            <h1 className="chat-context-bar__title">
              {currentConversation?.title ?? "新对话"}
            </h1>
          </div>
        </header>

        <MessageTimeline
          messages={chat.messages}
          pendingQuestion={chat.pendingQuestion}
          streaming={chat.streaming}
          streamingText={chat.streamingText}
          onRetry={(item) => void chat.send(item.content, item)}
          onOpenTrace={setTraceOpen}
        />

        <RetrievalScopeBar
          tags={chat.tags}
          value={chat.tagFilter}
          onChange={chat.setTagFilter}
        />

        <ChatComposer
          value={chat.input}
          streaming={chat.streaming}
          onChange={chat.setInput}
          onSubmit={chat.submitInput}
          onStop={chat.stopStreaming}
        />
      </main>

      <Drawer
        rootClassName="chat-conversation-drawer"
        title="会话"
        placement="left"
        size={320}
        open={narrow && conversationDrawerOpen}
        onClose={() => setConversationDrawerOpen(false)}
        destroyOnHidden
      >
        {rail}
      </Drawer>

      <Modal
        open={Boolean(renameTarget)}
        title="重命名会话"
        okText="保存"
        cancelText="取消"
        okButtonProps={{ disabled: !renameValue.trim() }}
        onOk={() => void saveRename()}
        onCancel={() => setRenameTarget(null)}
      >
        <Input
          value={renameValue}
          onChange={(event) => setRenameValue(event.target.value)}
          maxLength={120}
          aria-label="新会话标题"
          onPressEnter={() => void saveRename()}
        />
      </Modal>

      {traceOpen?.trace && (
        <TraceDrawer trace={traceOpen.trace} onClose={() => setTraceOpen(null)} />
      )}
    </div>
  );
}
