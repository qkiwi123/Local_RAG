import { useCallback, useMemo, useRef, useState } from "react";
import { api } from "../../api/client";
import type { UploadWarning } from "../../api/types";

export type UploadQueueStatus =
  | "staged"
  | "uploading"
  | "success"
  | "failed";

export interface UploadQueueItem {
  id: string;
  file: File;
  status: UploadQueueStatus;
  detail: string;
  warnings: UploadWarning[];
}
export interface UploadQueueAddResult {
  accepted: number;
  rejected: string[];
}

interface UseUploadQueueOptions {
  onUploaded?: () => void | Promise<void>;
}

const MAX_FILE_SIZE = 50 * 1024 * 1024;
const ALLOWED_EXTENSIONS = new Set(["pdf", "docx", "txt", "md", "pptx"]);

function extensionOf(fileName: string): string {
  return fileName.split(".").pop()?.toLowerCase() ?? "";
}

export function useUploadQueue({ onUploaded }: UseUploadQueueOptions = {}) {
  const [items, setItems] = useState<UploadQueueItem[]>([]);
  const [uploading, setUploading] = useState(false);
  const itemsRef = useRef<UploadQueueItem[]>([]);
  const sequenceRef = useRef(0);
  const runningRef = useRef(false);

  const commitItems = useCallback(
    (
      update:
        | UploadQueueItem[]
        | ((previous: UploadQueueItem[]) => UploadQueueItem[]),
    ) => {
      setItems((previous) => {
        const next = typeof update === "function" ? update(previous) : update;
        itemsRef.current = next;
        return next;
      });
    },
    [],
  );

  const addFiles = useCallback(
    (files: File[]): UploadQueueAddResult => {
      const rejected: string[] = [];
      const known = new Set(
        itemsRef.current.map(
          (item) =>
            `${item.file.name}\u0000${item.file.size}\u0000${item.file.lastModified}`,
        ),
      );
      const accepted: UploadQueueItem[] = [];

      for (const file of files) {
        const signature = `${file.name}\u0000${file.size}\u0000${file.lastModified}`;
        if (!ALLOWED_EXTENSIONS.has(extensionOf(file.name))) {
          rejected.push(`${file.name}：不支持该文件格式`);
          continue;
        }
        if (file.size > MAX_FILE_SIZE) {
          rejected.push(`${file.name}：超过 50 MB 限制`);
          continue;
        }
        if (known.has(signature)) {
          rejected.push(`${file.name}：已在上传队列中`);
          continue;
        }
        known.add(signature);
        sequenceRef.current += 1;
        accepted.push({
          id: `${signature}\u0000${sequenceRef.current}`,
          file,
          status: "staged",
          detail: "等待确认上传",
          warnings: [],
        });
      }

      if (accepted.length) {
        commitItems((previous) => [...previous, ...accepted]);
      }
      return { accepted: accepted.length, rejected };
    },
    [commitItems],
  );

  const uploadMatching = useCallback(
    async (status: "staged" | "failed", tags: string[]) => {
      if (runningRef.current) return;
      const targets = itemsRef.current.filter((item) => item.status === status);
      if (!targets.length) return;

      runningRef.current = true;
      setUploading(true);
      const targetIds = new Set(targets.map((item) => item.id));
      commitItems((previous) =>
        previous.map((item) =>
          targetIds.has(item.id)
            ? {
                ...item,
                status: "uploading",
                detail: status === "failed" ? "正在重试..." : "正在上传...",
                warnings: [],
              }
            : item,
        ),
      );

      let succeeded = false;
      try {
        for (const item of targets) {
          try {
            const response = await api.uploadDocument(item.file, tags);
            succeeded = true;
            commitItems((previous) =>
              previous.map((current) =>
                current.id === item.id
                  ? {
                      ...current,
                      status: "success",
                      detail: `已入库（${response.chunk_count} chunks）`,
                      warnings: response.warnings ?? [],
                    }
                  : current,
              ),
            );
          } catch (error) {
            commitItems((previous) =>
              previous.map((current) =>
                current.id === item.id
                  ? {
                      ...current,
                      status: "failed",
                      detail:
                        error instanceof Error ? error.message : "上传失败",
                      warnings: [],
                    }
                  : current,
              ),
            );
          }
        }
        if (succeeded) {
          await onUploaded?.();
        }
      } finally {
        runningRef.current = false;
        setUploading(false);
      }
    },
    [commitItems, onUploaded],
  );

  const removeItem = useCallback(
    (id: string) => {
      commitItems((previous) =>
        previous.filter(
          (item) => item.id !== id || item.status === "uploading",
        ),
      );
    },
    [commitItems],
  );

  const clearCompleted = useCallback(() => {
    commitItems((previous) =>
      previous.filter((item) => item.status !== "success"),
    );
  }, [commitItems]);

  const counts = useMemo(
    () => ({
      staged: items.filter((item) => item.status === "staged").length,
      uploading: items.filter((item) => item.status === "uploading").length,
      success: items.filter((item) => item.status === "success").length,
      failed: items.filter((item) => item.status === "failed").length,
    }),
    [items],
  );

  return {
    items,
    counts,
    uploading,
    addFiles,
    removeItem,
    clearCompleted,
    uploadStaged: (tags: string[]) => uploadMatching("staged", tags),
    retryFailed: (tags: string[]) => uploadMatching("failed", tags),
  };
}
