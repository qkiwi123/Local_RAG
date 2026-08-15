import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Col,
  Input,
  List,
  Modal,
  Popconfirm,
  Row,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  Upload,
  message,
} from "antd";
import {
  DeleteOutlined,
  EditOutlined,
  EyeOutlined,
  PlusOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import { api } from "../../api/client";
import type {
  DocumentItem,
  DocumentPreview,
  TagInfo,
  UploadWarning,
} from "../../api/types";
import { StructuredRenderer } from "../../components/StructuredRenderer";
import { TagTreeSelect } from "../../components/TagTreeSelect";

const { Text, Title } = Typography;

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export default function KnowledgePage() {
  const [tags, setTags] = useState<TagInfo[]>([]);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [documentFilter, setDocumentFilter] = useState<string[]>([]);
  const [loadingDocs, setLoadingDocs] = useState(false);

  const [stagedFiles, setStagedFiles] = useState<File[]>([]);
  const [uploadTags, setUploadTags] = useState<string[]>([]);
  const [newTagInput, setNewTagInput] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadResults, setUploadResults] = useState<
    { name: string; ok: boolean; detail: string; warnings: UploadWarning[] }[]
  >([]);

  const [createTagInput, setCreateTagInput] = useState("");
  const [renameTarget, setRenameTarget] = useState<TagInfo | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [preview, setPreview] = useState<DocumentPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [editDoc, setEditDoc] = useState<DocumentItem | null>(null);
  const [editTags, setEditTags] = useState<string[]>([]);
  const [editNewTags, setEditNewTags] = useState("");
  const loadedRef = useRef(false);

  const refreshTags = useCallback(async () => {
    try {
      setTags(await api.listTags());
    } catch {
      // 忽略
    }
  }, []);

  const refreshDocuments = useCallback(async (filter?: string[]) => {
    setLoadingDocs(true);
    try {
      const payload = await api.listDocuments(filter);
      setDocuments(payload.document_items);
    } catch (error) {
      message.error(`加载文档失败: ${(error as Error).message}`);
    } finally {
      setLoadingDocs(false);
    }
  }, []);

  useEffect(() => {
    if (loadedRef.current) return;
    loadedRef.current = true;
    void refreshTags();
    void refreshDocuments();
  }, [refreshDocuments, refreshTags]);

  const confirmUpload = async () => {
    if (!stagedFiles.length) return;
    setUploading(true);
    const results: typeof uploadResults = [];
    for (const file of stagedFiles) {
      try {
        const response = await api.uploadDocument(file, uploadTags);
        results.push({
          name: file.name,
          ok: true,
          detail: `已入库（${response.chunk_count} chunks）`,
          warnings: response.warnings,
        });
      } catch (error) {
        results.push({
          name: file.name,
          ok: false,
          detail: (error as Error).message,
          warnings: [],
        });
      }
    }
    setUploadResults(results);
    setStagedFiles([]);
    setUploading(false);
    await refreshDocuments();
    await refreshTags();
  };

  const createTag = async (raw: string, selectForUpload: boolean) => {
    const cleaned = raw.trim();
    if (!cleaned) {
      message.warning("请输入标签路径");
      return;
    }
    try {
      const created = await api.createTag(cleaned);
      if (selectForUpload && !uploadTags.includes(created.tag_path)) {
        setUploadTags([...uploadTags, created.tag_path]);
      }
      await refreshTags();
      message.success(`标签「${created.tag_path}」已创建`);
    } catch (error) {
      message.error(`新建标签失败: ${(error as Error).message}`);
    }
  };

  const deleteTag = async (tagPath: string) => {
    try {
      await api.deleteTag(tagPath);
      await refreshTags();
      await refreshDocuments();
      message.success(`标签「${tagPath}」已删除（文档保留）`);
    } catch (error) {
      message.error(`删除失败: ${(error as Error).message}`);
    }
  };

  const saveRename = async () => {
    if (!renameTarget) return;
    try {
      await api.renameTag(renameTarget.tag_path, renameValue.trim());
      setRenameTarget(null);
      await refreshTags();
      message.success("标签已重命名");
    } catch (error) {
      message.error(`重命名失败: ${(error as Error).message}`);
    }
  };

  const openPreview = async (document: DocumentItem) => {
    setPreviewLoading(true);
    setPreview(null);
    try {
      setPreview(await api.getDocumentPreview(document.document_id));
    } catch (error) {
      message.error(`预览失败: ${(error as Error).message}`);
    } finally {
      setPreviewLoading(false);
    }
  };

  const openEdit = (document: DocumentItem) => {
    setEditDoc(document);
    setEditTags(document.tags);
    setEditNewTags("");
  };

  const saveEdit = async () => {
    if (!editDoc) return;
    const merged = Array.from(
      new Set([
        ...editTags,
        ...editNewTags
          .replace(/，/g, ",")
          .split(",")
          .map((part) => part.trim())
          .filter(Boolean),
      ]),
    ).sort();
    try {
      await api.setDocumentTags(editDoc.document_id, merged);
      setEditDoc(null);
      await refreshDocuments();
      await refreshTags();
      message.success("标签已更新");
    } catch (error) {
      message.error(`保存失败: ${(error as Error).message}`);
    }
  };

  const deleteDocument = async (document: DocumentItem) => {
    try {
      await api.deleteDocument(document.document_id);
      await refreshDocuments();
      await refreshTags();
      message.success(`「${document.original_name}」已删除`);
    } catch (error) {
      message.error(`删除失败: ${(error as Error).message}`);
    }
  };

  return (
    <Row gutter={16}>
      <Col span={10}>
        <div style={{ background: "#fff", borderRadius: 8, padding: 16 }}>
          <Title level={5}>📤 上传文档（暂存 → 选标签 → 确认）</Title>
          <Upload.Dragger
            multiple
            beforeUpload={() => false}
            fileList={stagedFiles.map((file) => ({
              uid: `${file.name}-${file.size}`,
              name: file.name,
            }))}
            onRemove={(file) => {
              setStagedFiles((previous) =>
                previous.filter(
                  (item) => `${item.name}-${item.size}` !== file.uid,
                ),
              );
            }}
            onChange={({ fileList }) => {
              const files = fileList
                .map((item) => item.originFileObj as File)
                .filter(Boolean);
              setStagedFiles(files);
            }}
          >
            <p>
              <UploadOutlined /> 点击或拖拽选择文件（支持 PDF / Word / TXT / Markdown / PPTX）
            </p>
            <p style={{ color: "#8c8c8c" }}>
              选择文件只会暂存，点击「确认上传」才会解析入库
            </p>
          </Upload.Dragger>

          <Space direction="vertical" style={{ width: "100%", marginTop: 12 }}>
            <TagTreeSelect
              tags={tags}
              value={uploadTags}
              onChange={setUploadTags}
              placeholder="本次上传的标签（可选，可多选）"
            />
            <Space.Compact style={{ width: "100%" }}>
              <Input
                placeholder="上传时新建标签路径（创建后自动选中）"
                value={newTagInput}
                onChange={(e) => setNewTagInput(e.target.value)}
              />
              <Button
                icon={<PlusOutlined />}
                onClick={() => {
                  void createTag(newTagInput, true);
                  setNewTagInput("");
                }}
              >
                新建并选中
              </Button>
            </Space.Compact>
            <Button
              type="primary"
              block
              loading={uploading}
              disabled={!stagedFiles.length}
              onClick={() => void confirmUpload()}
            >
              {stagedFiles.length
                ? `✅ 确认上传（${stagedFiles.length} 个文件）`
                : "选择文件后暂存于此，点击确认上传才会解析入库"}
            </Button>
          </Space>

          {uploadResults.length > 0 && (
            <List
              size="small"
              style={{ marginTop: 12 }}
              dataSource={uploadResults}
              renderItem={(item) => (
                <List.Item>
                  <div>
                    <Text strong>{item.name}</Text>{" "}
                    {item.ok ? (
                      <Tag color="green">成功</Tag>
                    ) : (
                      <Tag color="red">失败</Tag>
                    )}
                    <Text type="secondary">{item.detail}</Text>
                    {item.warnings.map((warning) => (
                      <Alert
                        key={`${item.name}-${warning.code}`}
                        type="warning"
                        showIcon
                        style={{ marginTop: 4 }}
                        message={`${warning.location_label || ""} ${warning.message}`}
                      />
                    ))}
                  </div>
                </List.Item>
              )}
            />
          )}
        </div>

        <div style={{ background: "#fff", borderRadius: 8, padding: 16, marginTop: 16 }}>
          <Title level={5}>🏷 标签管理</Title>
          <Space.Compact style={{ width: "100%" }}>
            <Input
              placeholder="新标签路径（如：课程课件/数据结构）"
              value={createTagInput}
              onChange={(e) => setCreateTagInput(e.target.value)}
            />
            <Button
              icon={<PlusOutlined />}
              onClick={() => {
                void createTag(createTagInput, false);
                setCreateTagInput("");
              }}
            >
              新建标签
            </Button>
          </Space.Compact>
          <List
            size="small"
            style={{ marginTop: 8 }}
            dataSource={[...tags].sort((a, b) =>
              a.tag_path.localeCompare(b.tag_path),
            )}
            renderItem={(tag) => (
              <List.Item
                actions={[
                  <Button
                    key="rename"
                    size="small"
                    type="text"
                    icon={<EditOutlined />}
                    onClick={() => {
                      setRenameTarget(tag);
                      setRenameValue(tag.tag_path);
                    }}
                  />,
                  <Popconfirm
                    key="delete"
                    title={`删除「${tag.tag_path}」及其全部子标签？`}
                    description="文档本身会保留，仅解除关联。"
                    okText="确定删除"
                    cancelText="取消"
                    okButtonProps={{ danger: true }}
                    onConfirm={() => void deleteTag(tag.tag_path)}
                  >
                    <Button size="small" type="text" danger icon={<DeleteOutlined />} />
                  </Popconfirm>,
                ]}
              >
                <Text>
                  {tag.tag_path}
                  {tag.direct_document_count === tag.document_count
                    ? `（${tag.document_count} 文档）`
                    : `（本层 ${tag.direct_document_count} · 含子级 ${tag.document_count}）`}
                </Text>
              </List.Item>
            )}
          />
        </div>
      </Col>

      <Col span={14}>
        <div style={{ background: "#fff", borderRadius: 8, padding: 16 }}>
          <Row align="middle" justify="space-between" style={{ marginBottom: 12 }}>
            <Title level={5} style={{ margin: 0 }}>
              📋 已上传文档
            </Title>
            <Space>
              <TagTreeSelect
                tags={tags}
                value={documentFilter}
                onChange={(value) => {
                  setDocumentFilter(value);
                  void refreshDocuments(value);
                }}
                placeholder="按标签筛选文档"
              />
              <Button onClick={() => void refreshDocuments(documentFilter)}>
                刷新
              </Button>
            </Space>
          </Row>
          <Table<DocumentItem>
            rowKey="document_id"
            loading={loadingDocs}
            size="small"
            dataSource={documents}
            pagination={{ pageSize: 10 }}
            columns={[
              { title: "文档", dataIndex: "original_name", ellipsis: true },
              {
                title: "标签",
                dataIndex: "tags",
                render: (docTags: string[]) =>
                  docTags.length ? (
                    docTags.map((tag) => <Tag key={tag}>{tag}</Tag>)
                  ) : (
                    <Text type="secondary">未分类</Text>
                  ),
              },
              {
                title: "大小",
                dataIndex: "size_bytes",
                width: 90,
                render: (size: number) => formatBytes(size),
              },
              {
                title: "chunks",
                dataIndex: "chunk_count",
                width: 80,
              },
              {
                title: "上传时间",
                dataIndex: "created_at",
                width: 170,
                render: (value: string) => value.replace("T", " ").slice(0, 19),
              },
              {
                title: "操作",
                key: "actions",
                width: 200,
                render: (_, document) => (
                  <Space>
                    <Button
                      size="small"
                      icon={<EyeOutlined />}
                      onClick={() => void openPreview(document)}
                    >
                      预览
                    </Button>
                    <Button
                      size="small"
                      icon={<EditOutlined />}
                      onClick={() => openEdit(document)}
                    >
                      标签
                    </Button>
                    <Popconfirm
                      title={`删除「${document.original_name}」？`}
                      okText="确定删除"
                      cancelText="取消"
                      okButtonProps={{ danger: true }}
                      onConfirm={() => void deleteDocument(document)}
                    >
                      <Button size="small" danger icon={<DeleteOutlined />} />
                    </Popconfirm>
                  </Space>
                ),
              },
            ]}
          />
        </div>
      </Col>

      <Modal
        open={Boolean(renameTarget)}
        title="重命名标签（子树随前缀一并更新）"
        onOk={saveRename}
        onCancel={() => setRenameTarget(null)}
      >
        <Input
          value={renameValue}
          onChange={(e) => setRenameValue(e.target.value)}
        />
      </Modal>

      <Modal
        open={Boolean(editDoc)}
        title={`编辑标签 · ${editDoc?.original_name ?? ""}`}
        onOk={() => void saveEdit()}
        onCancel={() => setEditDoc(null)}
        width={560}
      >
        <TagTreeSelect
          tags={tags}
          value={editTags}
          onChange={setEditTags}
          placeholder="选择已有标签"
        />
        <Input
          style={{ marginTop: 12 }}
          placeholder="新标签（多个用逗号分隔，保存后会挂到当前文档）"
          value={editNewTags}
          onChange={(e) => setEditNewTags(e.target.value)}
        />
      </Modal>

      <Modal
        open={Boolean(preview) || previewLoading}
        title={`文档预览 · ${preview?.document_name ?? ""}`}
        onCancel={() => setPreview(null)}
        footer={<Button onClick={() => setPreview(null)}>关闭</Button>}
        width={880}
      >
        {previewLoading && (
          <div style={{ textAlign: "center", padding: 32 }}>
            <Spin tip="正在加载文档内容..." />
          </div>
        )}
        {preview && (
          <div style={{ maxHeight: "70vh", overflowY: "auto" }}>
            <Space style={{ marginBottom: 8 }}>
              {preview.tags.map((tag) => (
                <Tag key={tag}>{tag}</Tag>
              ))}
              <Text type="secondary">
                {preview.unit_count} 个单元 · {preview.parser_version}
              </Text>
            </Space>
            {preview.units.map((unit) => (
              <section
                key={unit.unit_id}
                style={{
                  marginBottom: 16,
                  padding: 12,
                  border: "1px solid #f0f0f0",
                  borderRadius: 8,
                }}
              >
                <Title level={5}>{unit.location.location_label}</Title>
                <StructuredRenderer blocks={unit.blocks} />
              </section>
            ))}
          </div>
        )}
      </Modal>
    </Row>
  );
}
