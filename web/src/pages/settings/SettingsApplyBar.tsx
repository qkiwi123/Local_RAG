import { Button, Space, Tag, Typography } from "antd";
import {
  RollbackOutlined,
  SaveOutlined,
  UndoOutlined,
} from "@ant-design/icons";

interface SettingsApplyBarProps {
  revision: number;
  dirty: boolean;
  saving: boolean;
  resetting: boolean;
  onDiscard: () => void;
  onReset: () => void;
  onSave: () => void;
}

export function SettingsApplyBar({
  revision,
  dirty,
  saving,
  resetting,
  onDiscard,
  onReset,
  onSave,
}: SettingsApplyBarProps) {
  return (
    <div className="settings-apply-bar" role="region" aria-label="设置操作">
      <Space orientation="vertical" size={0}>
        <Space wrap>
          <Typography.Text strong>配置 revision {revision}</Typography.Text>
          <Tag color={dirty ? "gold" : "green"}>
            {dirty ? "有未保存修改" : "已与后端同步"}
          </Tag>
        </Space>
        <Typography.Text type="secondary">
          问答与检索配置下一次提问生效；文档分块与视觉配置下一次上传生效。
        </Typography.Text>
      </Space>
      <Space wrap className="settings-apply-actions">
        <Button
          icon={<RollbackOutlined />}
          disabled={!dirty || saving || resetting}
          onClick={onDiscard}
        >
          放弃修改
        </Button>
        <Button
          icon={<UndoOutlined />}
          loading={resetting}
          disabled={saving}
          onClick={onReset}
        >
          恢复部署默认
        </Button>
        <Button
          type="primary"
          icon={<SaveOutlined />}
          loading={saving}
          disabled={!dirty || resetting}
          onClick={onSave}
        >
          保存并应用
        </Button>
      </Space>
    </div>
  );
}
