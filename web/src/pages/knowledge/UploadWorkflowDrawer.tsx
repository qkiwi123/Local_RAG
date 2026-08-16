import { useState } from "react";
import {
  Alert,
  Button,
  Input,
  Space,
  Tag,
  Typography,
  Upload,
  message,
} from "antd";
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  DeleteOutlined,
  FileAddOutlined,
  LoadingOutlined,
  PlusOutlined,
  ReloadOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import type { TagInfo, UploadWarning } from "../../api/types";
import { ResizableDrawer } from "../../components/ResizableDrawer";
import { TagTreeSelect } from "../../components/TagTreeSelect";
import type { UploadQueueItem } from "./useUploadQueue";

const { Text, Title } = Typography;

interface UploadWorkflowDrawerProps {
  open: boolean;
  tags: TagInfo[];
  selectedTags: string[];
  items: UploadQueueItem[];
  counts: {
    staged: number;
    uploading: number;
    success: number;
    failed: number;
  };
  uploading: boolean;
  onClose: () => void;
  onSelectedTagsChange: (value: string[]) => void;
  onAddFiles: (files: File[]) => { accepted: number; rejected: string[] };
  onRemoveItem: (id: string) => void;
  onClearCompleted: () => void;
  onUpload: () => void;
  onRetryFailed: () => void;
  onCreateAndSelectTag: (tagPath: string) => Promise<boolean>;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function statusMeta(item: UploadQueueItem) {
  switch (item.status) {
    case "uploading":
      return { color: "processing", label: "上传中", icon: <LoadingOutlined /> };
    case "success":
      return { color: "success", label: "成功", icon: <CheckCircleOutlined /> };
    case "failed":
      return { color: "error", label: "失败", icon: <CloseCircleOutlined /> };
    default:
      return { color: "default", label: "待上传", icon: <FileAddOutlined /> };
  }
}

function warningTitle(warning: UploadWarning): string {
  return warning.code.toLowerCase().includes("ocr")
    ? "AI 识别，可能存在误差"
    : "文档处理警告";
}

export function UploadWorkflowDrawer({
  open,
  tags,
  selectedTags,
  items,
  counts,
  uploading,
  onClose,
  onSelectedTagsChange,
  onAddFiles,
  onRemoveItem,
  onClearCompleted,
  onUpload,
  onRetryFailed,
  onCreateAndSelectTag,
}: UploadWorkflowDrawerProps) {
  const [newTag, setNewTag] = useState("");
  const totalPending = counts.staged + counts.uploading + counts.failed;

  const addFiles = (files: File[]) => {
    const result = onAddFiles(files);
    if (result.rejected.length) {
      message.warning(result.rejected.join("；"));
    }
  };

  const createAndSelect = async () => {
    const created = await onCreateAndSelectTag(newTag);
    if (created) setNewTag("");
  };

  return (
    <ResizableDrawer
      open={open}
      onClose={onClose}
      initialSize={580}
      minSize={380}
      storageKey="rag.drawer.upload.width"
      resizeHint="拖动左边界调整上传文档面板宽度"
      rootClassName="knowledge-task-drawer"
      title={
        <div className="knowledge-drawer-title">
          <span>上传文档</span>
          {totalPending > 0 && <Tag color="blue">待处理 {totalPending}</Tag>}
        </div>
      }
    >
      <div className="knowledge-upload-workflow">
        <section className="knowledge-drawer-section">
          <div className="knowledge-drawer-section__heading">
            <div>
              <Text className="knowledge-page__eyebrow">步骤 1</Text>
              <Title level={5}>选择文件</Title>
            </div>
            <Tag>最大 50 MB / 文件</Tag>
          </div>
          <Upload.Dragger
            className="knowledge-upload-dropzone"
            multiple
            accept=".pdf,.docx,.txt,.md,.pptx"
            beforeUpload={() => false}
            fileList={[]}
            showUploadList={false}
            onChange={({ fileList }) => {
              addFiles(
                fileList
                  .map((file) => file.originFileObj as File)
                  .filter(Boolean),
              );
            }}
          >
            <p className="knowledge-upload-dropzone__icon">
              <UploadOutlined />
            </p>
            <p className="knowledge-upload-dropzone__title">
              点击或拖拽文件到这里
            </p>
            <p className="knowledge-upload-dropzone__hint">
              支持 PDF、DOCX、TXT、Markdown、PPTX；选择后仅加入队列
            </p>
          </Upload.Dragger>
        </section>

        <section className="knowledge-drawer-section">
          <div className="knowledge-drawer-section__heading">
            <div>
              <Text className="knowledge-page__eyebrow">步骤 2</Text>
              <Title level={5}>设置本批标签</Title>
            </div>
          </div>
          <div className="knowledge-field">
            <Text className="knowledge-field__label" type="secondary">
              这些标签会应用到本次确认或重试的文件
            </Text>
            <TagTreeSelect
              tags={tags}
              value={selectedTags}
              onChange={onSelectedTagsChange}
              ariaLabel="本批上传标签"
              placeholder="选择已有标签（可选，可多选）"
            />
          </div>
          <Space.Compact className="knowledge-compact-input">
            <Input
              value={newTag}
              placeholder="新标签路径（如：课程/数据结构）"
              onChange={(event) => setNewTag(event.target.value)}
              onPressEnter={() => void createAndSelect()}
            />
            <Button
              icon={<PlusOutlined />}
              aria-label="新建并选中"
              onClick={() => void createAndSelect()}
            >
              新建并选中
            </Button>
          </Space.Compact>
        </section>

        <section className="knowledge-drawer-section knowledge-upload-queue">
          <div className="knowledge-drawer-section__heading">
            <div>
              <Text className="knowledge-page__eyebrow">步骤 3</Text>
              <Title level={5}>确认上传队列</Title>
            </div>
            {counts.success > 0 && (
              <Button size="small" type="text" onClick={onClearCompleted}>
                清除已完成
              </Button>
            )}
          </div>

          {items.length === 0 ? (
            <div className="knowledge-upload-queue__empty">
              <FileAddOutlined />
              <Text type="secondary">还没有文件，先从上方选择文件</Text>
            </div>
          ) : (
            <div className="knowledge-upload-queue__items" aria-live="polite">
              {items.map((item) => {
                const meta = statusMeta(item);
                return (
                  <article className="knowledge-upload-item" key={item.id}>
                    <div className="knowledge-upload-item__main">
                      <div className="knowledge-upload-item__name">
                        <Text strong ellipsis={{ tooltip: item.file.name }}>
                          {item.file.name}
                        </Text>
                        <Tag color={meta.color} icon={meta.icon}>
                          {meta.label}
                        </Tag>
                      </div>
                      <div className="knowledge-upload-item__detail">
                        <Text type="secondary">{formatBytes(item.file.size)} · </Text>
                        <Text type={item.status === "failed" ? "danger" : "secondary"}>
                          {item.detail}
                        </Text>
                      </div>
                    </div>
                    <Button
                      type="text"
                      danger
                      disabled={item.status === "uploading"}
                      aria-label={`移除上传文件 ${item.file.name}`}
                      icon={<DeleteOutlined />}
                      onClick={() => onRemoveItem(item.id)}
                    />
                    {item.warnings.map((warning, index) => (
                      <Alert
                        key={`${warning.code}-${warning.location_label}-${index}`}
                        className="knowledge-upload-warning"
                        type="warning"
                        showIcon
                        title={warningTitle(warning)}
                        description={`${warning.location_label ? `${warning.location_label}：` : ""}${warning.message}`}
                      />
                    ))}
                  </article>
                );
              })}
            </div>
          )}

          {counts.failed > 0 && (
            <Alert
              className="knowledge-upload-retry"
              type="error"
              showIcon
              title="失败文件保留，可重试"
              description="重试只会提交失败项，不会重复上传已经成功的文件。"
              action={
                <Button
                  size="small"
                  icon={<ReloadOutlined />}
                  aria-label="重试失败文件"
                  disabled={uploading}
                  onClick={onRetryFailed}
                >
                  重试失败文件
                </Button>
              }
            />
          )}

          <Button
            type="primary"
            size="large"
            block
            loading={uploading}
            disabled={counts.staged === 0}
            icon={<UploadOutlined />}
            aria-label={
              counts.staged
                ? `确认上传（${counts.staged} 个文件）`
                : "请选择文件后确认上传"
            }
            onClick={onUpload}
          >
            {counts.staged
              ? `确认上传（${counts.staged} 个文件）`
              : "请选择文件后确认上传"}
          </Button>
        </section>
      </div>
    </ResizableDrawer>
  );
}
