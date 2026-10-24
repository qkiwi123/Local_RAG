import { Button, Empty, Popconfirm, Tooltip } from "antd";
import {
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import type { ConversationSummary } from "../../api/types";

interface ConversationRailProps {
  conversations: ConversationSummary[];
  currentId: string | null;
  onCreate: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onRename: (conversation: ConversationSummary) => void;
  onNavigate?: () => void;
}

export function ConversationRail({
  conversations,
  currentId,
  onCreate,
  onSelect,
  onDelete,
  onRename,
  onNavigate,
}: ConversationRailProps) {
  return (
    <section className="chat-rail" aria-label="会话列表">
      <div className="chat-rail__header">
        <div>
          <div className="chat-rail__eyebrow">工作区</div>
          <h2 className="chat-rail__title">会话</h2>
        </div>
        <span className="chat-rail__count" aria-label={`${conversations.length} 个会话`}>
          {conversations.length}
        </span>
      </div>

      <div className="chat-rail__body">
      <div className="chat-rail__new-wrap">
      <Button
        type="primary"
        icon={<PlusOutlined />}
        onClick={() => { onCreate(); onNavigate?.(); }}
        block
        className="chat-rail__new"
      >
        新建对话
      </Button>
      </div>

      <div className="chat-conversation-list" role="list">
        {conversations.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无会话" />
        ) : (
          conversations.map((item) => {
            const active = item.conversation_id === currentId;
            return (
              <article
                key={item.conversation_id}
                className={`chat-conversation-item${
                  active ? " chat-conversation-item--active" : ""
                }`}
                role="listitem"
              >
                <button
                  type="button"
                  className="chat-conversation-item__select"
                  aria-current={active ? "page" : undefined}
                  aria-label={`打开会话「${item.title}」`}
                  title={item.title}
                  onClick={() => {
                    onSelect(item.conversation_id);
                    onNavigate?.();
                  }}
                >
                  <span className="chat-conversation-item__title">{item.title}</span>
                </button>

                <div className="chat-conversation-item__actions">
                  <Tooltip title="重命名">
                    <Button
                      size="small"
                      type="text"
                      aria-label={`重命名会话「${item.title}」`}
                      icon={<EditOutlined />}
                      onClick={() => onRename(item)}
                    />
                  </Tooltip>
                  <Popconfirm
                    title={`删除会话「${item.title}」？`}
                    description="删除后无法恢复。"
                    okText="确定删除"
                    cancelText="取消"
                    okButtonProps={{ danger: true }}
                    onConfirm={() => onDelete(item.conversation_id)}
                  >
                    <Tooltip title="删除">
                      <Button
                        size="small"
                        type="text"
                        danger
                        aria-label={`删除会话「${item.title}」`}
                        icon={<DeleteOutlined />}
                      />
                    </Tooltip>
                  </Popconfirm>
                </div>
              </article>
            );
          })
        )}
      </div>
      </div>
    </section>
  );
}
