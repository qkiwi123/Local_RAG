import { useCallback, useEffect, useRef, useState } from "react";
import { message } from "antd";
import { api } from "../../api/client";
import type { DocumentItem, DocumentPreview, TagInfo } from "../../api/types";
import { DocumentPreviewDrawer } from "./DocumentPreviewDrawer";
import { DocumentTable } from "./DocumentTable";
import { DocumentTagsModal } from "./DocumentTagsModal";
import { TagManagerDrawer } from "./TagManagerDrawer";
import { UploadWorkflowDrawer } from "./UploadWorkflowDrawer";
import { useUploadQueue } from "./useUploadQueue";
import "./knowledge-page.css";

type TaskDrawer = "upload" | "tags" | null;

export default function KnowledgePage() {
  const [tags, setTags] = useState<TagInfo[]>([]);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [documentFilter, setDocumentFilter] = useState<string[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [loadingDocuments, setLoadingDocuments] = useState(false);
  const [taskDrawer, setTaskDrawer] = useState<TaskDrawer>(null);
  const [uploadTags, setUploadTags] = useState<string[]>([]);
  const [editDocument, setEditDocument] = useState<DocumentItem | null>(null);
  const [preview, setPreview] = useState<DocumentPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const loadedRef = useRef(false);
  const documentsRequestRef = useRef(0);
  const previewRequestRef = useRef(0);
  const searchTimerRef = useRef<number | null>(null);

  const refreshTags = useCallback(async () => {
    try {
      setTags(await api.listTags());
    } catch (error) {
      message.error(
        `加载标签失败: ${error instanceof Error ? error.message : "未知错误"}`,
      );
    }
  }, []);

  const refreshDocuments = useCallback(
    async (query: string = searchQuery, filter: string[] = documentFilter) => {
      const requestId = documentsRequestRef.current + 1;
      documentsRequestRef.current = requestId;
      setLoadingDocuments(true);
      try {
        const payload = await api.listDocuments(query, filter);
        if (documentsRequestRef.current === requestId) {
          setDocuments(payload.document_items);
        }
      } catch (error) {
        if (documentsRequestRef.current === requestId) {
          message.error(
            `加载文档失败: ${error instanceof Error ? error.message : "未知错误"}`,
          );
        }
      } finally {
        if (documentsRequestRef.current === requestId) {
          setLoadingDocuments(false);
        }
      }
    },
    [searchQuery, documentFilter],
  );

  const handleSearchChange = (value: string) => {
    setSearchQuery(value);
    if (searchTimerRef.current !== null) {
      window.clearTimeout(searchTimerRef.current);
    }
    searchTimerRef.current = window.setTimeout(() => {
      void refreshDocuments(value, documentFilter);
    }, 300);
  };

  useEffect(() => {
    if (loadedRef.current) return;
    loadedRef.current = true;
    void refreshTags();
    void refreshDocuments();
  }, [refreshDocuments, refreshTags]);

  const uploadQueue = useUploadQueue({
    onUploaded: async () => {
      await Promise.all([refreshDocuments(searchQuery, documentFilter), refreshTags()]);
    },
  });

  const createTag = async (
    rawPath: string,
    selectForUpload = false,
  ): Promise<boolean> => {
    const cleaned = rawPath.trim().replace(/^\/+|\/+$/g, "");
    if (!cleaned) {
      message.warning("请输入标签路径");
      return false;
    }
    try {
      const created = await api.createTag(cleaned);
      if (selectForUpload) {
        setUploadTags((previous) =>
          previous.includes(created.tag_path)
            ? previous
            : [...previous, created.tag_path],
        );
      }
      await refreshTags();
      message.success(`标签「${created.tag_path}」已创建`);
      return true;
    } catch (error) {
      message.error(
        `新建标签失败: ${error instanceof Error ? error.message : "未知错误"}`,
      );
      return false;
    }
  };

  const renameTag = async (
    oldPath: string,
    rawNewPath: string,
  ): Promise<boolean> => {
    const newPath = rawNewPath.trim().replace(/^\/+|\/+$/g, "");
    if (!newPath) {
      message.warning("请输入新的标签路径");
      return false;
    }
    try {
      await api.renameTag(oldPath, newPath);
      const replacePrefix = (value: string) =>
        value === oldPath || value.startsWith(`${oldPath}/`)
          ? `${newPath}${value.slice(oldPath.length)}`
          : value;
      setUploadTags((previous) =>
        Array.from(new Set(previous.map(replacePrefix))),
      );
      const nextFilter = Array.from(
        new Set(documentFilter.map(replacePrefix)),
      );
      setDocumentFilter(nextFilter);
      await Promise.all([refreshTags(), refreshDocuments(searchQuery, nextFilter)]);
      message.success("标签及其子树已重命名");
      return true;
    } catch (error) {
      message.error(
        `重命名失败: ${error instanceof Error ? error.message : "未知错误"}`,
      );
      return false;
    }
  };

  const deleteTag = async (tagPath: string) => {
    try {
      await api.deleteTag(tagPath);
      const keepUnrelated = (value: string) =>
        value !== tagPath && !value.startsWith(`${tagPath}/`);
      setUploadTags((previous) => previous.filter(keepUnrelated));
      const nextFilter = documentFilter.filter(keepUnrelated);
      setDocumentFilter(nextFilter);
      await Promise.all([refreshTags(), refreshDocuments(searchQuery, nextFilter)]);
      message.success(`标签「${tagPath}」已删除，文档仍保留`);
    } catch (error) {
      message.error(
        `删除标签失败: ${error instanceof Error ? error.message : "未知错误"}`,
      );
    }
  };

  const openPreview = async (document: DocumentItem) => {
    const requestId = previewRequestRef.current + 1;
    previewRequestRef.current = requestId;
    setPreview(null);
    setPreviewLoading(true);
    try {
      const payload = await api.getDocumentPreview(document.document_id);
      if (previewRequestRef.current === requestId) setPreview(payload);
    } catch (error) {
      if (previewRequestRef.current === requestId) {
        message.error(
          `预览失败: ${error instanceof Error ? error.message : "未知错误"}`,
        );
      }
    } finally {
      if (previewRequestRef.current === requestId) setPreviewLoading(false);
    }
  };

  const closePreview = () => {
    previewRequestRef.current += 1;
    setPreview(null);
    setPreviewLoading(false);
  };

  const saveDocumentTags = async (
    document: DocumentItem,
    nextTags: string[],
  ): Promise<boolean> => {
    try {
      await api.setDocumentTags(document.document_id, nextTags);
      await Promise.all([refreshDocuments(searchQuery, documentFilter), refreshTags()]);
      message.success("文档标签已更新");
      return true;
    } catch (error) {
      message.error(
        `保存标签失败: ${error instanceof Error ? error.message : "未知错误"}`,
      );
      return false;
    }
  };

  const deleteDocument = async (document: DocumentItem) => {
    try {
      await api.deleteDocument(document.document_id);
      await Promise.all([refreshDocuments(searchQuery, documentFilter), refreshTags()]);
      message.success(`「${document.original_name}」已删除`);
    } catch (error) {
      message.error(
        `删除文档失败: ${error instanceof Error ? error.message : "未知错误"}`,
      );
    }
  };

  const handleFilterChange = (value: string[]) => {
    setDocumentFilter(value);
    void refreshDocuments(searchQuery, value);
  };

  return (
    <main className="knowledge-page">
      <DocumentTable
        documents={documents}
        tags={tags}
        filter={documentFilter}
        searchValue={searchQuery}
        onSearchChange={handleSearchChange}
        loading={loadingDocuments}
        queuedCount={
          uploadQueue.counts.staged +
          uploadQueue.counts.uploading +
          uploadQueue.counts.failed
        }
        failedCount={uploadQueue.counts.failed}
        onFilterChange={handleFilterChange}
        onRefresh={() => void refreshDocuments(searchQuery, documentFilter)}
        onOpenUpload={() => setTaskDrawer("upload")}
        onOpenTags={() => setTaskDrawer("tags")}
        onPreview={(document) => void openPreview(document)}
        onEditTags={setEditDocument}
        onDelete={(document) => void deleteDocument(document)}
      />

      <UploadWorkflowDrawer
        open={taskDrawer === "upload"}
        tags={tags}
        selectedTags={uploadTags}
        items={uploadQueue.items}
        counts={uploadQueue.counts}
        uploading={uploadQueue.uploading}
        onClose={() => setTaskDrawer(null)}
        onSelectedTagsChange={setUploadTags}
        onAddFiles={uploadQueue.addFiles}
        onRemoveItem={uploadQueue.removeItem}
        onClearCompleted={uploadQueue.clearCompleted}
        onUpload={() => void uploadQueue.uploadStaged(uploadTags)}
        onRetryFailed={() => void uploadQueue.retryFailed(uploadTags)}
        onCreateAndSelectTag={(tagPath) => createTag(tagPath, true)}
      />

      <TagManagerDrawer
        open={taskDrawer === "tags"}
        tags={tags}
        onClose={() => setTaskDrawer(null)}
        onCreate={(tagPath) => createTag(tagPath)}
        onRename={renameTag}
        onDelete={(tagPath) => void deleteTag(tagPath)}
      />

      <DocumentTagsModal
        document={editDocument}
        tags={tags}
        onClose={() => setEditDocument(null)}
        onSave={saveDocumentTags}
      />

      <DocumentPreviewDrawer
        preview={preview}
        loading={previewLoading}
        onClose={closePreview}
      />
    </main>
  );
}
