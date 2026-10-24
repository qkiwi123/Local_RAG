import { useState } from "react";
import { Alert, Button, Input, Space, Tag, Typography, Upload } from "antd";
import {
  CheckCircleOutlined, ClearOutlined, CloseCircleOutlined, DeleteOutlined,
  FileAddOutlined, LoadingOutlined, PlusOutlined, ReloadOutlined, UploadOutlined,
} from "@ant-design/icons";
import type { TagInfo, UploadWarning } from "../../api/types";
import { ResizableDrawer } from "../../components/ResizableDrawer";
import { TagTreeSelect } from "../../components/TagTreeSelect";
import type { UploadBatchResult, UploadQueueAddResult, UploadQueueItem } from "./useUploadQueue";

const { Text, Title } = Typography;

interface UploadWorkflowDrawerProps {
  open: boolean;
  tags: TagInfo[];
  selectedTags: string[];
  items: UploadQueueItem[];
  counts: { staged: number; uploading: number; success: number; failed: number };
  uploading: boolean;
  lastBatch: UploadBatchResult | null;
  onClose: () => void;
  onSelectedTagsChange: (value: string[]) => void;
  onAddFiles: (files: File[]) => UploadQueueAddResult;
  onRemoveItem: (id: string) => void;
  onClearPending: () => void;
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
    case "uploading": return { color: "processing", label: "处理中", icon: <LoadingOutlined /> };
    case "success": return { color: "success", label: "成功", icon: <CheckCircleOutlined /> };
    case "failed": return { color: "error", label: "失败", icon: <CloseCircleOutlined /> };
    default: return { color: "default", label: "待上传", icon: <FileAddOutlined /> };
  }
}

function warningTitle(warning: UploadWarning): string {
  return warning.code.toLowerCase().includes("ocr") ? "AI 识别，可能存在误差" : "文档处理警告";
}

export function UploadWorkflowDrawer({
  open, tags, selectedTags, items, counts, uploading, lastBatch, onClose,
  onSelectedTagsChange, onAddFiles, onRemoveItem, onClearPending,
  onUpload, onRetryFailed, onCreateAndSelectTag,
}: UploadWorkflowDrawerProps) {
  const [newTag, setNewTag] = useState("");
  const [selectionResult, setSelectionResult] = useState<UploadQueueAddResult | null>(null);
  const totalPending = counts.staged + counts.uploading + counts.failed;
  const createAndSelect = async () => {
    if (await onCreateAndSelectTag(newTag)) setNewTag("");
  };
  const confirmLabel = uploading ? "正在上传文件"
    : counts.staged ? `确认上传（${counts.staged} 个文件）` : "请选择文件后确认上传";

  return (
    <ResizableDrawer open={open} onClose={onClose} initialSize={620} minSize={380}
      storageKey="rag.drawer.upload.width" resizeHint="拖动左边界调整上传文档面板宽度"
      rootClassName="knowledge-task-drawer knowledge-upload-drawer"
      styles={{ body: { overflow: "hidden", padding: 0 } }}
      title={<div className="knowledge-drawer-title"><span>上传文档</span>{totalPending > 0 && <Tag color="blue">待处理 {totalPending}</Tag>}</div>}
    >
      <div className="knowledge-upload-panel">
        <div className="knowledge-upload-actions" role="region" aria-label="待上传文件操作">
          <div className="knowledge-upload-actions__buttons">
            <Button type="primary" loading={uploading} disabled={!counts.staged || uploading}
              icon={<UploadOutlined />} aria-label={confirmLabel}
              onClick={() => { setSelectionResult(null); onUpload(); }}>
              {uploading ? "正在上传…" : counts.staged ? `确认上传（${counts.staged}）` : "确认上传"}
            </Button>
            {counts.failed > 0 && <Button icon={<ReloadOutlined />} disabled={uploading}
              aria-label="重试失败文件" onClick={() => { setSelectionResult(null); onRetryFailed(); }}>重试失败项</Button>}
            <Button icon={<ClearOutlined />} disabled={uploading || !items.length}
              aria-label="清空待上传文件" onClick={() => { onClearPending(); setSelectionResult(null); }}>清空队列</Button>
          </div>
          <p>{uploading ? `正在处理本批文件 · 已完成 ${counts.success} 个` : counts.staged ? `${counts.staged} 个待上传${counts.failed ? ` · ${counts.failed} 个失败` : ""}，确认后开始入库` : counts.failed ? `${counts.failed} 个文件未上传成功，可重试或清空` : "支持批量添加 · 成功后自动移出队列"}</p>
        </div>

        <div className="knowledge-upload-workflow">
          {lastBatch && <div className="knowledge-upload-result" aria-label="本批上传结果">
            <Alert type={lastBatch.failed || lastBatch.warnings.length ? "warning" : "success"} showIcon
              title={`上传完成：${lastBatch.succeeded} 个成功${lastBatch.failed ? `，${lastBatch.failed} 个失败` : ""}`}
              description={lastBatch.failed ? "成功项已自动移出队列，失败项保留在下方。" : items.length ? "成功项已自动移出队列，新添加的文件仍可继续上传。" : "上传队列已清空，可以继续添加文件。"} />
            {lastBatch.warnings.length > 0 && <div className="knowledge-upload-result__warnings">
              {lastBatch.warnings.map((entry, fileIndex) => <div key={fileIndex}>
                <Text strong>{entry.fileName}</Text>
                {entry.warnings.map((warning, index) => <Alert key={index} type="warning" showIcon
                  title={warningTitle(warning)} description={`${warning.location_label ? `${warning.location_label}：` : ""}${warning.message}`} />)}
              </div>)}
            </div>}
          </div>}

          <section className="knowledge-drawer-section knowledge-upload-files">
            <div className="knowledge-drawer-section__heading">
              <div><Text className="knowledge-page__eyebrow">添加与检查</Text><Title level={5}>选择文件</Title></div>
              <Tag>最大 50 MB / 文件</Tag>
            </div>
            <Upload.Dragger className="knowledge-upload-dropzone" multiple accept=".pdf,.docx,.txt,.md,.pptx"
              beforeUpload={(file, batch) => {
                // The upload library calls this for every file in the same selection.
                if (file === batch[0]) setSelectionResult(onAddFiles(batch));
                return Upload.LIST_IGNORE;
              }} fileList={[]} showUploadList={false}>
              <p className="knowledge-upload-dropzone__icon"><UploadOutlined /></p>
              <p className="knowledge-upload-dropzone__title">点击或拖拽文件到这里</p>
              <p className="knowledge-upload-dropzone__hint">PDF、DOCX、TXT、Markdown、PPTX · 选择后加入下方队列</p>
            </Upload.Dragger>

            {!!selectionResult?.rejected.length && <Alert className="knowledge-upload-selection-warning" type="warning" showIcon
              title={`已加入 ${selectionResult.accepted} 个文件，${selectionResult.rejected.length} 个文件未加入队列`}
              description={<details><summary>查看未加入原因</summary><ul aria-label="未加入队列的文件">{selectionResult.rejected.map((reason, index) => <li key={index}>{reason}</li>)}</ul></details>} />}

            <div className="knowledge-upload-tags">
              <div className="knowledge-upload-tags__heading"><Text strong>本批标签</Text><Text type="secondary">选填 · 应用于本次上传或重试</Text></div>
              <TagTreeSelect tags={tags} value={selectedTags} onChange={onSelectedTagsChange}
                ariaLabel="本批上传标签" placeholder="选择已有标签（可选，可多选）" />
              <Space.Compact className="knowledge-compact-input">
                <Input value={newTag} placeholder="新标签路径（如：课程/数据结构）"
                  onChange={(event) => setNewTag(event.target.value)} onPressEnter={() => void createAndSelect()} />
                <Button icon={<PlusOutlined />} aria-label="新建并选中" onClick={() => void createAndSelect()}>新建并选中</Button>
              </Space.Compact>
            </div>

            <div className="knowledge-upload-queue">
              <div className="knowledge-upload-queue__heading"><Title level={5}>上传队列</Title><Text type="secondary">{items.length} 个文件</Text></div>
              {items.length === 0 ? <div className="knowledge-upload-queue__empty"><FileAddOutlined /><Text type="secondary">还没有文件，先从上方选择文件</Text></div>
                : <div className="knowledge-upload-queue__items" aria-live="polite">{items.map((item) => {
                  const meta = statusMeta(item);
                  return <article className="knowledge-upload-item" key={item.id}>
                    <div className="knowledge-upload-item__main">
                      <div className="knowledge-upload-item__name"><Text strong ellipsis={{ tooltip: item.file.name }}>{item.file.name}</Text><Tag color={meta.color} icon={meta.icon}>{meta.label}</Tag></div>
                      <div className="knowledge-upload-item__detail"><Text type="secondary">{formatBytes(item.file.size)} · </Text><Text type={item.status === "failed" ? "danger" : "secondary"}>{item.detail}</Text></div>
                    </div>
                    <Button type="text" danger disabled={item.status === "uploading"} aria-label={`移除上传文件 ${item.file.name}`} icon={<DeleteOutlined />} onClick={() => onRemoveItem(item.id)} />
                  </article>;
                })}</div>}
              {counts.failed > 0 && <Alert className="knowledge-upload-retry" type="error" showIcon title="失败文件保留，可重试" description="在顶部重试失败项，或清空队列后重新选择文件。" />}
            </div>
          </section>
        </div>
      </div>
    </ResizableDrawer>
  );
}
