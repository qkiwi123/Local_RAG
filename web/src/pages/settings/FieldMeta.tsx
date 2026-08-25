import { Space, Tag, Typography } from "antd";
import type {
  SettingsActivation,
  SettingsFieldSource,
} from "../../api/types";

const sourceLabels: Record<SettingsFieldSource, string> = {
  environment: "部署默认",
  runtime: "页面覆盖",
  system: "系统固定",
};

const activationLabels: Record<SettingsActivation, string> = {
  next_request: "下一请求生效",
  restart_required: "需要重启",
  reindex_required: "需要重建索引",
  unavailable: "当前不可用",
};

interface FieldMetaProps {
  source?: SettingsFieldSource;
  activation?: SettingsActivation;
  note?: string;
  inactive?: boolean;
}

export function FieldMeta({
  source,
  activation,
  note,
  inactive = false,
}: FieldMetaProps) {
  return (
    <Space size={[4, 4]} wrap className="settings-field-meta">
      {source && <Tag>{sourceLabels[source]}</Tag>}
      {activation && (
        <Tag color={activation === "unavailable" ? "default" : "blue"}>
          {activationLabels[activation]}
        </Tag>
      )}
      {inactive && <Tag>当前模式不使用</Tag>}
      {note && <Typography.Text type="secondary">{note}</Typography.Text>}
    </Space>
  );
}
