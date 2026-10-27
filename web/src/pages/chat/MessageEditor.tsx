import { useState } from "react";
import { Alert, Input, Modal } from "antd";
import type { ConversationMessage, TagInfo } from "../../api/types";
import { RetrievalScopeBar } from "./RetrievalScopeBar";

interface MessageEditorProps {
  message: ConversationMessage;
  tags: TagInfo[];
  laterRounds: number;
  onCancel: () => void;
  onSave: (question: string, tags: string[]) => void;
}

export function MessageEditor({ message, tags, laterRounds, onCancel, onSave }: MessageEditorProps) {
  const [question, setQuestion] = useState(message.content);
  const [scope, setScope] = useState<string[]>(message.retrieval_tags ?? []);
  const missing = scope.filter((path) => !tags.some((tag) => tag.tag_path === path));
  return (
    <Modal open title="编辑问题与检索范围" width={680} rootClassName="chat-message-editor"
      okText="保存并重新生成" cancelText="取消" onCancel={onCancel}
      okButtonProps={{ disabled: !question.trim() || missing.length > 0 }}
      onOk={() => onSave(question.trim(), scope)}>
      <label className="chat-message-editor__label" htmlFor="edit-chat-question">问题内容</label>
      <Input.TextArea id="edit-chat-question" autoFocus aria-label="编辑已发送的问题" value={question}
        maxLength={8000} showCount autoSize={{ minRows: 4, maxRows: 10 }}
        onChange={(event) => setQuestion(event.target.value)} />
      <section className="chat-message-editor__scope" aria-label="本条消息的检索范围">
        <div><strong>本条检索范围</strong><p>只应用于这次重新生成，可单独修改。</p></div>
        <RetrievalScopeBar editMode tags={tags} value={scope} onChange={setScope} />
      </section>
      {message.retrieval_tags == null && <Alert type="info" showIcon
        title="这条旧消息未记录检索范围，请确认本次范围后再生成。" />}
      {missing.length > 0 && <Alert type="warning" showIcon title="部分原标签已不存在，请先修改范围。"
        description={missing.join("、")} />}
      <p className="chat-message-editor__notice">{laterRounds > 0
        ? `生成成功后，将替换本轮内容并清除后续 ${laterRounds} 轮对话。`
        : "生成成功后，将替换本轮问题与回答。"}生成失败或停止时，原记录保留。</p>
    </Modal>
  );
}
