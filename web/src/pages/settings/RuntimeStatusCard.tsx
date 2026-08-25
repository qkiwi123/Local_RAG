import {
  Button,
  Card,
  Descriptions,
  Result,
  Space,
  Tag,
  Typography,
} from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import type { ModelRuntimeStatus, SettingsRuntimeStatus } from "../../api/types";

interface RuntimeStatusCardProps {
  status: SettingsRuntimeStatus | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
}

function modelState(model: ModelRuntimeStatus) {
  if (!model.enabled) return <Tag>未启用</Tag>;
  if (!model.loaded) return <Tag color="blue">未加载</Tag>;
  if (model.verified) return <Tag color="green">设备已验证</Tag>;
  return <Tag color="orange">设备待确认</Tag>;
}

export function RuntimeStatusCard({
  status,
  loading,
  error,
  onRefresh,
}: RuntimeStatusCardProps) {
  return (
    <Card
      className="settings-card"
      title="本地推理与索引状态"
      loading={loading && !status}
      extra={
        <Button
          size="small"
          icon={<ReloadOutlined />}
          loading={loading}
          onClick={onRefresh}
        >
          刷新状态
        </Button>
      }
    >
      {error && !status ? (
        <Result
          status="warning"
          title="运行状态加载失败"
          subTitle={error}
          extra={<Button onClick={onRefresh}>重试</Button>}
        />
      ) : status ? (
        <Descriptions
          column={{ xs: 1, md: 2, xl: 3 }}
          size="small"
          items={[
            {
              key: "embedding-model",
              label: "Embedding",
              children: (
                <Space orientation="vertical" size={2}>
                  <Typography.Text>{status.embedding.model}</Typography.Text>
                  {modelState(status.embedding)}
                </Space>
              ),
            },
            {
              key: "embedding-device",
              label: "Embedding 设备",
              children: `${status.embedding.configured_device} → ${
                status.embedding.actual_device || "尚未加载"
              }`,
            },
            {
              key: "reranker-model",
              label: "Reranker",
              children: (
                <Space orientation="vertical" size={2}>
                  <Typography.Text>{status.reranker.model}</Typography.Text>
                  {modelState(status.reranker)}
                </Space>
              ),
            },
            {
              key: "reranker-device",
              label: "Reranker 设备",
              children: `${status.reranker.configured_device} → ${
                status.reranker.actual_device || "尚未加载"
              }${status.reranker.fp16 === null ? "" : ` · FP16 ${status.reranker.fp16 ? "开" : "关"}`}`,
            },
            {
              key: "cuda",
              label: "CUDA",
              children: status.torch.cuda_available ? (
                <Space wrap>
                  <Tag color="green">可用</Tag>
                  <span>{status.torch.cuda_runtime || "版本未知"}</span>
                  <span>{status.torch.gpu_names.join("、")}</span>
                </Space>
              ) : (
                <Tag>不可用</Tag>
              ),
            },
            {
              key: "torch",
              label: "PyTorch",
              children: status.torch.version || "未知",
            },
            {
              key: "index",
              label: "索引",
              children: `${status.index.parser_version} · ${status.index.chunk_strategy} · revision ${status.index.process_index_revision}`,
            },
            {
              key: "chunk",
              label: "Chunk 配置",
              children: `${status.index.chunk_size} / overlap ${status.index.chunk_overlap} / max tokens ${status.index.chunk_max_tokens}`,
            },
            {
              key: "storage",
              label: "存储摘要",
              children: (
                <Space orientation="vertical" size={2}>
                  <Tag color={status.index.storage_profile === "test" ? "gold" : "blue"}>
                    {status.index.storage_profile === "test" ? "测试隔离" : "生产配置"}
                  </Tag>
                  <Typography.Text type="secondary">
                    {status.index.chroma_directory} · {status.index.metadata_database}
                  </Typography.Text>
                </Space>
              ),
            },
          ]}
        />
      ) : null}
    </Card>
  );
}
