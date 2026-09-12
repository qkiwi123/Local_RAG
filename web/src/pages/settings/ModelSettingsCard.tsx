import {
  Alert,
  Button,
  Card,
  Col,
  Divider,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Row,
  Select,
  Space,
  Switch,
  Tag,
  Typography,
  type FormInstance,
} from "antd";
import { ApiOutlined, DeleteOutlined } from "@ant-design/icons";
import type { SettingsReadResponse } from "../../api/types";
import type { ApiKeyIntent, SettingsFormValues } from "./formTypes";
import { FieldMeta } from "./FieldMeta";

const thinkingModeOptions = [
  { value: "provider_default", label: "服务商默认" },
  { value: "enabled", label: "明确开启" },
  { value: "disabled", label: "明确关闭" },
];

const reasoningEffortOptions = [
  { value: "provider_default", label: "服务商默认" },
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
  { value: "max", label: "Max" },
];

interface ModelSettingsCardProps {
  form: FormInstance<SettingsFormValues>;
  settings: SettingsReadResponse;
  apiKeyIntent: ApiKeyIntent;
  testing: boolean;
  onApiKeyIntentChange: (intent: ApiKeyIntent) => void;
  onTestConnection: () => void;
}

export function ModelSettingsCard({
  form,
  settings,
  apiKeyIntent,
  testing,
  onApiKeyIntentChange,
  onTestConnection,
}: ModelSettingsCardProps) {
  const enabled = Form.useWatch(["llm", "enabled"], form);
  const keyStatus = settings.llm.api_key;
  const keyPlaceholder = keyStatus.configured
    ? `已配置（${keyStatus.source === "runtime" ? "页面密钥" : "环境变量"}${
        keyStatus.masked_suffix ? `，尾号 ${keyStatus.masked_suffix}` : ""
      }）`
    : "尚未配置 API Key";

  const meta = (field: string, note?: string) => (
    <FieldMeta
      source={settings.field_sources[`llm.${field}`]}
      activation={settings.field_effects[`llm.${field}`]}
      note={note}
    />
  );

  return (
    <Card
      className="settings-card"
      title="问答模型"
      extra={
        <Tag color={enabled ? "green" : "default"}>
          {enabled ? "生成模型已启用" : "Retrieval-only"}
        </Tag>
      }
    >
      <Row gutter={[20, 0]}>
        <Col xs={24} lg={8}>
          <Form.Item
            name={["llm", "enabled"]}
            label="启用生成模型"
            valuePropName="checked"
            extra={meta("enabled", "关闭后仍执行检索、引用和 Trace，不调用模型 API。")}
          >
            <Switch checkedChildren="启用" unCheckedChildren="关闭" />
          </Form.Item>
        </Col>
        <Col xs={24} md={12} lg={8}>
          <Form.Item
            name={["llm", "provider"]}
            label="服务商"
            rules={[{ required: true, message: "请选择服务商" }]}
            extra={meta("provider")}
          >
            <Select
              options={[
                { value: "deepseek", label: "DeepSeek" },
                { value: "openai_compatible", label: "OpenAI-compatible" },
              ]}
            />
          </Form.Item>
        </Col>
        <Col xs={24} md={12} lg={8}>
          <Form.Item
            name={["llm", "model"]}
            label="模型名"
            rules={[
              { required: true, whitespace: true, message: "请输入模型名" },
              { max: 200, message: "模型名不能超过 200 个字符" },
            ]}
            extra={meta("model")}
          >
            <Input placeholder="例如 deepseek-chat" autoComplete="off" />
          </Form.Item>
        </Col>
      </Row>

      <Row gutter={[20, 0]}>
        <Col xs={24} lg={14}>
          <Form.Item
            name={["llm", "base_url"]}
            label="Base URL"
            rules={[
              { required: true, whitespace: true, message: "请输入 Base URL" },
              { type: "url", message: "请输入有效的 HTTP(S) 地址" },
            ]}
            extra={meta("base_url", "不允许在 URL 中填写用户名、密码、查询参数或片段。")}
          >
            <Input placeholder="https://api.deepseek.com" autoComplete="url" />
          </Form.Item>
        </Col>
        <Col xs={24} lg={10}>
          <Form.Item
            name={["llm", "api_key_draft"]}
            label="API Key"
            extra={
              <Space orientation="vertical" size={4}>
                <FieldMeta
                  source={settings.field_sources["llm.api_key"]}
                  activation={settings.field_effects["llm.api_key"]}
                  note="输入框永不回填旧密钥；留空表示保留当前密钥。"
                />
                {apiKeyIntent === "clear" && (
                  <Typography.Text type="danger">
                    保存时将清除运行时密钥覆盖。
                  </Typography.Text>
                )}
              </Space>
            }
          >
            <Input.Password
              aria-label="API Key"
              placeholder={keyPlaceholder}
              autoComplete="new-password"
              onChange={(event) =>
                onApiKeyIntentChange(event.target.value ? "replace" : "keep")
              }
            />
          </Form.Item>
          <Popconfirm
            title="确认清除运行时 API Key？"
            description="保存后将删除页面密钥；若环境变量仍有 Key，会恢复使用环境变量。"
            okText="确认清除"
            cancelText="取消"
            onConfirm={() => {
              form.setFieldValue(["llm", "api_key_draft"], "");
              onApiKeyIntentChange("clear");
            }}
          >
            <Button
              danger
              size="small"
              icon={<DeleteOutlined />}
              disabled={!keyStatus.configured || apiKeyIntent === "clear"}
            >
              清除已保存密钥
            </Button>
          </Popconfirm>
        </Col>
      </Row>

      <Divider titlePlacement="start">最终回答生成</Divider>
      <Alert
        type="info"
        showIcon
        title="参数保持通用且始终可编辑"
        description="不同服务商或模型可能忽略、不支持或重新解释某些参数；系统不会因开启思考而锁定 Temperature，调用失败时请结合 Trace 与上游文档检查兼容性。"
      />
      <Row gutter={[20, 0]}>
        <Col xs={24} md={12} lg={6}>
          <Form.Item
            name={["llm", "answer_thinking_mode"]}
            label="回答思考模式"
            rules={[{ required: true, message: "请选择回答思考模式" }]}
            extra={meta("answer_thinking_mode", "服务商默认表示不发送显式开关。")}
          >
            <Select options={thinkingModeOptions} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12} lg={6}>
          <Form.Item
            name={["llm", "answer_reasoning_effort"]}
            label="回答思考强度"
            rules={[{ required: true, message: "请选择回答思考强度" }]}
            extra={meta("answer_reasoning_effort", "可独立设置；是否生效由上游模型决定。")}
          >
            <Select options={reasoningEffortOptions} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12} lg={6}>
          <Form.Item
            name={["llm", "temperature"]}
            label="回答 Temperature"
            rules={[{ required: true, message: "请输入 Temperature" }]}
            extra={meta("temperature", "范围 0–2；部分思考模型可能忽略，但字段不会被禁用。")}
          >
            <InputNumber min={0} max={2} step={0.1} precision={2} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12} lg={6}>
          <Form.Item
            name={["llm", "max_tokens"]}
            label="回答最大完成 Tokens"
            rules={[{ required: true, message: "请输入回答最大完成 Tokens" }]}
            extra={meta("max_tokens", "范围 1–32768；思考模型通常会让思考和正文共享该预算。")}
          >
            <InputNumber min={1} max={32768} precision={0} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12} lg={6}>
          <Form.Item
            name={["llm", "timeout_seconds"]}
            label="回答请求超时（秒）"
            rules={[{ required: true, message: "请输入超时时间" }]}
            extra={meta("timeout_seconds", "范围 1–600 秒；连接测试也使用该超时。")}
          >
            <InputNumber min={1} max={600} step={1} />
          </Form.Item>
        </Col>
      </Row>

      <Divider titlePlacement="start">查询改写</Divider>
      <Row gutter={[20, 0]}>
        <Col xs={24} md={12} lg={6}>
          <Form.Item
            name={["llm", "rewrite_enabled"]}
            label="启用查询改写"
            valuePropName="checked"
            extra={meta("rewrite_enabled", "开启后，仅在有历史消息的多轮会话中尝试改写。")}
          >
            <Switch checkedChildren="自动" unCheckedChildren="关闭" />
          </Form.Item>
        </Col>
        <Col xs={24} md={12} lg={6}>
          <Form.Item
            name={["llm", "rewrite_thinking_mode"]}
            label="改写思考模式"
            rules={[{ required: true, message: "请选择改写思考模式" }]}
            extra={meta("rewrite_thinking_mode", "默认明确关闭，以降低短任务延迟和空正文风险。")}
          >
            <Select options={thinkingModeOptions} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12} lg={6}>
          <Form.Item
            name={["llm", "rewrite_reasoning_effort"]}
            label="改写思考强度"
            rules={[{ required: true, message: "请选择改写思考强度" }]}
            extra={meta("rewrite_reasoning_effort", "始终可编辑；冲突组合可能被上游忽略或拒绝。")}
          >
            <Select options={reasoningEffortOptions} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12} lg={6}>
          <Form.Item
            name={["llm", "rewrite_timeout_seconds"]}
            label="改写请求超时（秒）"
            rules={[{ required: true, message: "请输入改写超时时间" }]}
            extra={meta("rewrite_timeout_seconds", "范围 1–600 秒；超时后回退原问题。")}
          >
            <InputNumber min={1} max={600} step={1} />
          </Form.Item>
        </Col>
      </Row>
      <Row gutter={[20, 0]}>
        <Col xs={24} md={12} lg={6}>
          <Form.Item
            name={["llm", "rewrite_temperature"]}
            label="改写 Temperature"
            rules={[{ required: true, message: "请输入改写 Temperature" }]}
            extra={meta("rewrite_temperature", "范围 0–2；默认 0，字段不会因思考模式而禁用。")}
          >
            <InputNumber min={0} max={2} step={0.1} precision={2} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12} lg={6}>
          <Form.Item
            name={["llm", "rewrite_max_tokens"]}
            label="改写最大完成 Tokens"
            rules={[{ required: true, message: "请输入改写最大完成 Tokens" }]}
            extra={meta("rewrite_max_tokens", "范围 1–32768；默认 200。")}
          >
            <InputNumber min={1} max={32768} precision={0} />
          </Form.Item>
        </Col>
      </Row>

      <Space wrap>
        <Button
          icon={<ApiOutlined />}
          loading={testing}
          onClick={onTestConnection}
        >
          测试连接
        </Button>
        <Typography.Text type="secondary">
          使用当前未保存草稿发起一次最小真实模型请求，可能产生少量费用。
        </Typography.Text>
      </Space>
    </Card>
  );
}
