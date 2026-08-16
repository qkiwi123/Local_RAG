import { Empty, Space, Spin, Tag, Typography } from "antd";
import { FileSearchOutlined } from "@ant-design/icons";
import type { DocumentPreview } from "../../api/types";
import { ResizableDrawer } from "../../components/ResizableDrawer";
import { StructuredRenderer } from "../../components/StructuredRenderer";

const { Text, Title } = Typography;

interface DocumentPreviewDrawerProps {
  preview: DocumentPreview | null;
  loading: boolean;
  onClose: () => void;
}

export function DocumentPreviewDrawer({
  preview,
  loading,
  onClose,
}: DocumentPreviewDrawerProps) {
  return (
    <ResizableDrawer
      open={loading || Boolean(preview)}
      onClose={onClose}
      initialSize={880}
      minSize={420}
      storageKey="rag.drawer.preview.width"
      resizeHint="拖动左边界调整文档预览宽度"
      rootClassName="knowledge-preview-drawer"
      title={`文档预览${preview ? ` · ${preview.document_name}` : ""}`}
    >
      {loading && (
        <div className="knowledge-preview__loading">
          <Spin size="large" description="正在加载文档内容..." />
        </div>
      )}

      {!loading && preview && (
        <div className="knowledge-preview">
          <div className="knowledge-preview__summary">
            <div className="knowledge-preview__meta">
              <Space wrap size={[4, 6]}>
                {preview.tags.length ? (
                  preview.tags.map((tag) => <Tag key={tag}>{tag}</Tag>)
                ) : (
                  <Tag>未分类</Tag>
                )}
              </Space>
              <Text type="secondary">
                {preview.unit_count} 个内容单元 · {preview.parser_version}
              </Text>
            </div>
          </div>

          {preview.units.length ? (
            <div className="knowledge-preview__units">
              {preview.units.map((unit, index) => (
                <section className="knowledge-preview-unit" key={unit.unit_id}>
                  <div className="knowledge-preview-unit__heading">
                    <span className="knowledge-preview-unit__index">
                      {String(index + 1).padStart(2, "0")}
                    </span>
                    <div>
                      <Text className="knowledge-page__eyebrow">内容位置</Text>
                      <Title level={5}>{unit.location.location_label}</Title>
                    </div>
                  </div>
                  <StructuredRenderer blocks={unit.blocks} />
                </section>
              ))}
            </div>
          ) : (
            <Empty
              image={<FileSearchOutlined />}
              description="该文档暂时没有可预览的结构化内容"
            />
          )}
        </div>
      )}
    </ResizableDrawer>
  );
}
