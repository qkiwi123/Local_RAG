import { useEffect, useRef, useState } from "react";
import { Button, List, Modal, Spin, Typography } from "antd";
import { api } from "../api/client";
import type { Citation, CitationPreviewPayload } from "../api/types";
import { centerCitationTarget } from "./citationScroll";
import { StructuredRenderer } from "./StructuredRenderer";

const { Text } = Typography;

function CitationPreviewModal({
  citation,
  onClose,
}: {
  citation: Citation;
  onClose: () => void;
}) {
  const [loading, setLoading] = useState(true);
  const [preview, setPreview] = useState<CitationPreviewPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [modalReady, setModalReady] = useState(false);
  const previewContainerRef = useRef<HTMLDivElement>(null);
  const userNavigatedRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setPreview(null);
    setError(null);
    userNavigatedRef.current = false;
    api
      .getCitationPreview(citation.document_id!, citation.chunk_id!)
      .then((payload) => {
        if (!cancelled) {
          setPreview(payload);
          setLoading(false);
        }
      })
      .catch((err: { status?: number; message?: string }) => {
        if (!cancelled) {
          const deleted =
            err.status === 404 ? "历史引用来源已删除，无法查看原文" : null;
          setError(deleted || err.message || "预览加载失败");
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [citation]);

  useEffect(() => {
    const container = previewContainerRef.current;
    if (!preview || !container) {
      return;
    }

    let firstFrame = 0;
    let secondFrame = 0;
    const centerTarget = () => {
      if (modalReady && !userNavigatedRef.current) {
        centerCitationTarget(container);
      }
    };
    const stopAutoPosition = () => {
      userNavigatedRef.current = true;
    };

    // 两帧不代表缩放动画已结束：必须同时等待打开完成事件和预览数据。
    // 动画中的 getBoundingClientRect 是缩放坐标，不能与 scrollTop 混用。
    if (modalReady) {
      firstFrame = window.requestAnimationFrame(() => {
        secondFrame = window.requestAnimationFrame(centerTarget);
      });
    }

    // OCR 原图可能在首次定位后才加载并撑高前置内容，加载完成时校正一次。
    const pendingImages = Array.from(container.querySelectorAll("img")).filter(
      (image) => !image.complete,
    );
    for (const image of pendingImages) {
      image.addEventListener("load", centerTarget, { once: true });
    }

    // 用户开始操作后不再自动抢回滚动位置。
    container.addEventListener("wheel", stopAutoPosition, { passive: true });
    container.addEventListener("touchstart", stopAutoPosition, { passive: true });
    container.addEventListener("pointerdown", stopAutoPosition);
    container.addEventListener("keydown", stopAutoPosition);

    return () => {
      window.cancelAnimationFrame(firstFrame);
      window.cancelAnimationFrame(secondFrame);
      for (const image of pendingImages) {
        image.removeEventListener("load", centerTarget);
      }
      container.removeEventListener("wheel", stopAutoPosition);
      container.removeEventListener("touchstart", stopAutoPosition);
      container.removeEventListener("pointerdown", stopAutoPosition);
      container.removeEventListener("keydown", stopAutoPosition);
    };
  }, [preview, modalReady]);

  return (
    <Modal
      open
      afterOpenChange={setModalReady}
      title={`引用原文 · ${citation.document_name}`}
      onCancel={onClose}
      footer={
        <Button onClick={onClose}>关闭</Button>
      }
      width={880}
    >
      {loading && (
        <div style={{ textAlign: "center", padding: 32 }}>
          <Spin tip="正在加载原文..." />
        </div>
      )}
      {error && <Text type="danger">{error}</Text>}
      {preview && (
        <div
          ref={previewContainerRef}
          aria-label="引用原文内容"
          tabIndex={0}
          style={{ maxHeight: "70vh", overflowY: "auto" }}
        >
          {preview.units.map((unit) => {
            const isTarget = unit.unit_id === preview.target_unit_id;
            return (
              <section
                key={unit.unit_id}
                data-citation-preview-target={isTarget ? "true" : undefined}
                style={{ marginBottom: 16, padding: 12, border: "1px solid #f0f0f0", borderRadius: 8 }}
              >
                <Typography.Title level={5}>
                  {isTarget ? "🎯 " : ""}
                  {unit.location.location_label}
                </Typography.Title>
                <StructuredRenderer
                  blocks={unit.blocks}
                  highlightStart={isTarget ? preview.highlight_start : undefined}
                  highlightEnd={isTarget ? preview.highlight_end : undefined}
                  documentId={citation.document_id ?? undefined}
                  showOcrImages
                />
              </section>
            );
          })}
        </div>
      )}
    </Modal>
  );
}

export function CitationList({ citations }: { citations: Citation[] }) {
  const [openCitation, setOpenCitation] = useState<Citation | null>(null);

  if (!citations.length) {
    return null;
  }

  return (
    <List
      size="small"
      header={<Text type="secondary">📎 参考来源</Text>}
      dataSource={citations}
      renderItem={(citation) => (
        <List.Item
          actions={
            citation.preview_available
              ? [
                  <Button
                    key="preview"
                    size="small"
                    onClick={() => setOpenCitation(citation)}
                  >
                    🔍 查看原文
                  </Button>,
                ]
              : []
          }
        >
          <div>
            <Text strong>
              【来源{citation.source_id}】《{citation.document_name}》
              {citation.location_label || "位置未知"}
            </Text>
            <br />
            {citation.excerpt ? (
              <Text type="secondary" ellipsis style={{ maxWidth: 640 }}>
                {citation.excerpt}
              </Text>
            ) : null}
          </div>
        </List.Item>
      )}
    >
      {openCitation &&
        openCitation.document_id &&
        openCitation.chunk_id && (
          <CitationPreviewModal
            citation={openCitation}
            onClose={() => setOpenCitation(null)}
          />
        )}
    </List>
  );
}
