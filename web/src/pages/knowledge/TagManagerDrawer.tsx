import { useMemo, useState } from "react";
import {
  Button,
  Empty,
  Input,
  Modal,
  Popconfirm,
  Space,
  Tag,
  Typography,
} from "antd";
import {
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  TagsOutlined,
} from "@ant-design/icons";
import type { TagInfo } from "../../api/types";
import { ResizableDrawer } from "../../components/ResizableDrawer";
import { buildTagTree, type TagTreeNode } from "../../utils/tagTree";

const { Paragraph, Text } = Typography;

interface TagManagerDrawerProps {
  open: boolean;
  tags: TagInfo[];
  onClose: () => void;
  onCreate: (tagPath: string) => Promise<boolean>;
  onRename: (oldPath: string, newPath: string) => Promise<boolean>;
  onDelete: (tagPath: string) => void;
}

interface TagBranchProps {
  node: TagTreeNode;
  depth: number;
  tagByPath: Map<string, TagInfo>;
  onRename: (tag: TagInfo) => void;
  onDelete: (tagPath: string) => void;
}

function TagBranch({
  node,
  depth,
  tagByPath,
  onRename,
  onDelete,
}: TagBranchProps) {
  const info = tagByPath.get(node.value);
  if (!info) return null;
  const hasChildren = node.children.length > 0;

  return (
    <div className="knowledge-tag-branch" role="treeitem" aria-level={depth + 1}>
      <div
        className="knowledge-tag-node"
        style={{ "--tag-depth": depth } as React.CSSProperties}
      >
        <span className="knowledge-tag-node__guide" aria-hidden="true" />
        <div className="knowledge-tag-node__content">
          <div className="knowledge-tag-node__label">
            <Text strong>{info.name}</Text>
            {info.tag_path.includes("/") && (
              <Text type="secondary" className="knowledge-tag-node__path">
                {info.tag_path}
              </Text>
            )}
          </div>
          <Tag>
            {info.direct_document_count === info.document_count
              ? `${info.document_count} 文档`
              : `本层 ${info.direct_document_count} · 含子级 ${info.document_count}`}
          </Tag>
        </div>
        <Space size={2} className="knowledge-tag-node__actions">
          <Button
            size="small"
            type="text"
            aria-label={`重命名标签 ${info.tag_path}`}
            icon={<EditOutlined />}
            onClick={() => onRename(info)}
          />
          <Popconfirm
            title={`删除「${info.tag_path}」${hasChildren ? "及其全部子标签" : ""}？`}
            description="文档本身会保留，仅解除标签关联。"
            okText="确定删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={() => onDelete(info.tag_path)}
          >
            <Button
              size="small"
              type="text"
              danger
              aria-label={`删除标签 ${info.tag_path}`}
              icon={<DeleteOutlined />}
            />
          </Popconfirm>
        </Space>
      </div>
      {hasChildren && (
        <div className="knowledge-tag-children" role="group">
          {node.children.map((child) => (
            <TagBranch
              key={child.value}
              node={child}
              depth={depth + 1}
              tagByPath={tagByPath}
              onRename={onRename}
              onDelete={onDelete}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export function TagManagerDrawer({
  open,
  tags,
  onClose,
  onCreate,
  onRename,
  onDelete,
}: TagManagerDrawerProps) {
  const [newTag, setNewTag] = useState("");
  const [renameTarget, setRenameTarget] = useState<TagInfo | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const tagTree = useMemo(() => buildTagTree(tags), [tags]);
  const tagByPath = useMemo(
    () => new Map(tags.map((tag) => [tag.tag_path, tag])),
    [tags],
  );

  const create = async () => {
    if (await onCreate(newTag)) setNewTag("");
  };

  const saveRename = async () => {
    if (!renameTarget) return;
    if (await onRename(renameTarget.tag_path, renameValue)) {
      setRenameTarget(null);
    }
  };

  return (
    <>
      <ResizableDrawer
        open={open}
        onClose={onClose}
        initialSize={580}
        minSize={380}
        storageKey="rag.drawer.tags.width"
        resizeHint="拖动左边界调整标签管理面板宽度"
        rootClassName="knowledge-task-drawer"
        title={
          <div className="knowledge-drawer-title">
            <span>标签管理</span>
            <Tag>{tags.length} 个</Tag>
          </div>
        }
      >
        <div className="knowledge-tag-manager">
          <Paragraph type="secondary" className="knowledge-tag-manager__intro">
            标签使用“/”形成层级。重命名父标签会同步更新子树；删除标签不会删除文档。
          </Paragraph>
          <Space.Compact className="knowledge-compact-input">
            <Input
              value={newTag}
              placeholder="新标签路径（如：课程/数据结构）"
              onChange={(event) => setNewTag(event.target.value)}
              onPressEnter={() => void create()}
            />
            <Button
              icon={<PlusOutlined />}
              aria-label="新建标签"
              onClick={() => void create()}
            >
              新建标签
            </Button>
          </Space.Compact>

          <div className="knowledge-tag-tree" role="tree" aria-label="标签层级">
            {tagTree.length ? (
              tagTree.map((node) => (
                <TagBranch
                  key={node.value}
                  node={node}
                  depth={0}
                  tagByPath={tagByPath}
                  onRename={(tag) => {
                    setRenameTarget(tag);
                    setRenameValue(tag.tag_path);
                  }}
                  onDelete={onDelete}
                />
              ))
            ) : (
              <Empty
                image={<TagsOutlined />}
                description="还没有标签，可从上方新建"
              />
            )}
          </div>
        </div>
      </ResizableDrawer>

      <Modal
        open={Boolean(renameTarget)}
        title="重命名标签"
        okText="保存"
        cancelText="取消"
        onOk={() => void saveRename()}
        onCancel={() => setRenameTarget(null)}
      >
        <Paragraph type="secondary">
          子标签会随路径前缀一并更新，请填写完整的新路径。
        </Paragraph>
        <Input
          value={renameValue}
          onChange={(event) => setRenameValue(event.target.value)}
          onPressEnter={() => void saveRename()}
        />
      </Modal>
    </>
  );
}
