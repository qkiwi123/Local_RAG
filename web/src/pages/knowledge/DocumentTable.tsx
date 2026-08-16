import { useEffect, useState } from "react";
import {
  Button,
  Empty,
  Grid,
  Pagination,
  Popconfirm,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from "antd";
import {
  DeleteOutlined,
  EditOutlined,
  EyeOutlined,
  FolderOpenOutlined,
  ReloadOutlined,
  TagsOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import type { DocumentItem, TagInfo } from "../../api/types";
import { TagTreeSelect } from "../../components/TagTreeSelect";

const { Text, Title } = Typography;

interface DocumentTableProps {
  documents: DocumentItem[];
  tags: TagInfo[];
  filter: string[];
  loading: boolean;
  queuedCount: number;
  failedCount: number;
  onFilterChange: (value: string[]) => void;
  onRefresh: () => void;
  onOpenUpload: () => void;
  onOpenTags: () => void;
  onPreview: (document: DocumentItem) => void;
  onEditTags: (document: DocumentItem) => void;
  onDelete: (document: DocumentItem) => void;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatTime(value: string): string {
  return value.replace("T", " ").slice(0, 19);
}

interface DocumentActionsProps {
  document: DocumentItem;
  showDeleteLabel?: boolean;
  onPreview: (document: DocumentItem) => void;
  onEditTags: (document: DocumentItem) => void;
  onDelete: (document: DocumentItem) => void;
}

function DocumentActions({
  document,
  showDeleteLabel = false,
  onPreview,
  onEditTags,
  onDelete,
}: DocumentActionsProps) {
  return (
    <Space size={4} wrap>
      <Button
        size="small"
        type="text"
        icon={<EyeOutlined />}
        aria-label={`预览文档 ${document.original_name}`}
        onClick={() => onPreview(document)}
      >
        预览
      </Button>
      <Button
        size="small"
        type="text"
        icon={<EditOutlined />}
        aria-label={`编辑文档标签 ${document.original_name}`}
        onClick={() => onEditTags(document)}
      >
        标签
      </Button>
      <Popconfirm
        title={`删除「${document.original_name}」？`}
        description="删除后无法恢复。"
        okText="确定删除"
        cancelText="取消"
        okButtonProps={{ danger: true }}
        onConfirm={() => onDelete(document)}
      >
        <Button
          size="small"
          type="text"
          danger
          aria-label={`删除文档 ${document.original_name}`}
          icon={<DeleteOutlined />}
        >
          {showDeleteLabel ? "删除" : null}
        </Button>
      </Popconfirm>
    </Space>
  );
}

export function DocumentTable({
  documents,
  tags,
  filter,
  loading,
  queuedCount,
  failedCount,
  onFilterChange,
  onRefresh,
  onOpenUpload,
  onOpenTags,
  onPreview,
  onEditTags,
  onDelete,
}: DocumentTableProps) {
  const screens = Grid.useBreakpoint();
  const compactDocuments = screens.lg === false;
  const [compactPage, setCompactPage] = useState(1);
  const pageSize = 10;
  const compactDocumentsPage = documents.slice(
    (compactPage - 1) * pageSize,
    compactPage * pageSize,
  );

  useEffect(() => {
    setCompactPage(1);
  }, [filter]);

  useEffect(() => {
    const lastPage = Math.max(1, Math.ceil(documents.length / pageSize));
    setCompactPage((current) => Math.min(current, lastPage));
  }, [documents.length]);

  const uploadLabel = failedCount
    ? `上传文档（${failedCount} 个失败）`
    : queuedCount
      ? `上传文档（队列 ${queuedCount}）`
      : "上传文档";

  return (
    <section
      className="knowledge-documents"
      aria-labelledby="knowledge-documents-title"
    >
      <div className="knowledge-documents__hero">
        <div className="knowledge-documents__heading">
          <Text className="knowledge-page__eyebrow">知识库管理</Text>
          <div className="knowledge-documents__title-row">
            <Title
              level={2}
              id="knowledge-documents-title"
              className="knowledge-documents__title"
            >
              知识文档
            </Title>
            <Tag color="blue">{documents.length} 个</Tag>
          </div>
          <Text type="secondary">
            按标签筛选文档，并在当前工作区完成预览、分类与维护。
          </Text>
        </div>
        <div className="knowledge-documents__actions">
          <Button
            icon={<TagsOutlined />}
            aria-label="标签管理"
            onClick={onOpenTags}
          >
            标签管理
          </Button>
          <Button
            type="primary"
            icon={<UploadOutlined />}
            aria-label="上传文档"
            onClick={onOpenUpload}
          >
            {uploadLabel}
          </Button>
        </div>
      </div>

      <div className="knowledge-documents__toolbar">
        <div className="knowledge-documents__filter">
          <Text className="knowledge-field__label" type="secondary">
            标签筛选
          </Text>
          <TagTreeSelect
            tags={tags}
            value={filter}
            onChange={onFilterChange}
            ariaLabel="文档标签筛选"
            placeholder="按标签筛选文档"
          />
        </div>
        <Button icon={<ReloadOutlined />} aria-label="刷新文档" onClick={onRefresh}>
          刷新
        </Button>
      </div>

      {compactDocuments ? (
        <div className="knowledge-document-cards" aria-label="文档列表">
          <Spin spinning={loading}>
            {compactDocumentsPage.length ? (
              <div className="knowledge-document-cards__items">
                {compactDocumentsPage.map((document) => (
                  <article
                    className="knowledge-document-card"
                    key={document.document_id}
                  >
                    <div className="knowledge-document-card__heading">
                      <span className="knowledge-document-icon">
                        <FolderOpenOutlined />
                      </span>
                      <div className="knowledge-document-card__title">
                        <Text strong>{document.original_name}</Text>
                        <Text type="secondary">
                          {formatBytes(document.size_bytes)} · {document.chunk_count} 个片段
                        </Text>
                      </div>
                    </div>
                    <div className="knowledge-document-tags">
                      {document.tags.length ? (
                        document.tags.map((tag) => <Tag key={tag}>{tag}</Tag>)
                      ) : (
                        <Text type="secondary">未分类</Text>
                      )}
                    </div>
                    <Text type="secondary" className="knowledge-document-card__time">
                      上传于 {formatTime(document.created_at)}
                    </Text>
                    <div className="knowledge-document-card__actions">
                      <DocumentActions
                        document={document}
                        showDeleteLabel
                        onPreview={onPreview}
                        onEditTags={onEditTags}
                        onDelete={onDelete}
                      />
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="当前筛选条件下没有文档"
              >
                <Button type="primary" onClick={onOpenUpload}>
                  上传第一份文档
                </Button>
              </Empty>
            )}
          </Spin>
          {documents.length > pageSize && (
            <Pagination
              simple
              current={compactPage}
              pageSize={pageSize}
              total={documents.length}
              onChange={setCompactPage}
            />
          )}
        </div>
      ) : (
        <div className="knowledge-documents__table">
          <Table<DocumentItem>
          rowKey="document_id"
          loading={loading}
          size="middle"
          dataSource={documents}
          pagination={{ pageSize: 10, hideOnSinglePage: true }}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="当前筛选条件下没有文档"
              >
                <Button type="primary" onClick={onOpenUpload}>
                  上传第一份文档
                </Button>
              </Empty>
            ),
          }}
          columns={[
            {
              title: "文档",
              dataIndex: "original_name",
              minWidth: 220,
              ellipsis: true,
              render: (name: string) => (
                <Space size={10}>
                  <span className="knowledge-document-icon">
                    <FolderOpenOutlined />
                  </span>
                  <Text strong ellipsis={{ tooltip: name }}>
                    {name}
                  </Text>
                </Space>
              ),
            },
            {
              title: "标签",
              dataIndex: "tags",
              minWidth: 220,
              render: (documentTags: string[]) =>
                documentTags.length ? (
                  <div className="knowledge-document-tags">
                    {documentTags.map((tag) => (
                      <Tag key={tag}>{tag}</Tag>
                    ))}
                  </div>
                ) : (
                  <Text type="secondary">未分类</Text>
                ),
            },
            {
              title: "大小",
              dataIndex: "size_bytes",
              width: 100,
              render: (size: number) => formatBytes(size),
            },
            {
              title: "片段",
              dataIndex: "chunk_count",
              width: 82,
            },
            {
              title: "上传时间",
              dataIndex: "created_at",
              width: 170,
              render: formatTime,
            },
            {
              title: "操作",
              key: "actions",
              fixed: "right",
              width: 198,
              render: (_, document) => (
                <DocumentActions
                  document={document}
                  onPreview={onPreview}
                  onEditTags={onEditTags}
                  onDelete={onDelete}
                />
              ),
            },
          ]}
          scroll={{ x: 1080 }}
          />
        </div>
      )}
    </section>
  );
}
