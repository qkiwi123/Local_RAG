import { useEffect, useState } from "react";
import { Input, Modal, Typography } from "antd";
import type { DocumentItem, TagInfo } from "../../api/types";
import { TagTreeSelect } from "../../components/TagTreeSelect";

const { Paragraph } = Typography;

interface DocumentTagsModalProps {
  document: DocumentItem | null;
  tags: TagInfo[];
  onClose: () => void;
  onSave: (document: DocumentItem, tags: string[]) => Promise<boolean>;
}

export function DocumentTagsModal({
  document,
  tags,
  onClose,
  onSave,
}: DocumentTagsModalProps) {
  const [selected, setSelected] = useState<string[]>([]);
  const [newTags, setNewTags] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!document) return;
    setSelected(document.tags);
    setNewTags("");
  }, [document]);

  const save = async () => {
    if (!document) return;
    const merged = Array.from(
      new Set([
        ...selected,
        ...newTags
          .replace(/，/g, ",")
          .split(",")
          .map((part) => part.trim())
          .filter(Boolean),
      ]),
    ).sort();
    setSaving(true);
    try {
      if (await onSave(document, merged)) onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={Boolean(document)}
      title={`编辑标签 · ${document?.original_name ?? ""}`}
      width={600}
      okText="保存标签"
      cancelText="取消"
      confirmLoading={saving}
      onOk={() => void save()}
      onCancel={onClose}
    >
      <Paragraph type="secondary">
        可选择已有标签，也可输入尚未创建的标签路径；多个新标签请用逗号分隔。
      </Paragraph>
      <TagTreeSelect
        tags={tags}
        value={selected}
        onChange={setSelected}
        ariaLabel="文档标签编辑"
        placeholder="选择已有标签"
      />
      <Input
        className="knowledge-document-tags-modal__input"
        placeholder="新标签（如：课程/数据结构, 项目/案例）"
        value={newTags}
        onChange={(event) => setNewTags(event.target.value)}
        onPressEnter={() => void save()}
      />
    </Modal>
  );
}
