import { useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Collapse,
  Divider,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Radio,
  Row,
  Select,
  Space,
  Switch,
  Tag,
  Typography,
  type FormInstance,
} from "antd";
import { ApiOutlined, DeleteOutlined } from "@ant-design/icons";
import type {
  ApiKeyStatus,
  SettingsReadResponse,
  VisionSource,
  VisionTestTask,
} from "../../api/types";
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

const providerOptions = [
  { value: "deepseek", label: "DeepSeek" },
  { value: "openai_compatible", label: "OpenAI-compatible" },
];

interface VisionSettingsCardProps {
  form: FormInstance<SettingsFormValues>;
  settings: SettingsReadResponse;
  apiKeyIntent: ApiKeyIntent;
  testing: boolean;
  llmDirty: boolean;
  onApiKeyIntentChange: (intent: ApiKeyIntent) => void;
  onTestConnection: (task: VisionTestTask) => void;
}

interface VisionTaskSettingsCardProps {
  task: VisionTestTask;
  title: string;
  meta: (field: string, note?: string) => React.ReactNode;
}

function providerLabel(provider: "deepseek" | "openai_compatible") {
  return provider === "deepseek" ? "DeepSeek" : "OpenAI-compatible";
}

function sourceLabel(source: VisionSource) {
  return source === "follow_llm" ? "跟随问答模型" : "自定义视觉模型";
}

function keySourceLabel(source: ApiKeyStatus["source"]) {
  return source === "runtime"
    ? "页面密钥"
    : source === "environment"
      ? "环境变量"
      : "未配置";
}

function keyStatusLabel(status: ApiKeyStatus) {
  if (!status.configured) return "API Key 未配置";
  return `API Key 已配置（${keySourceLabel(status.source)}${
    status.masked_suffix ? `，尾号 ${status.masked_suffix}` : ""
  }）`;
}

function VisionTaskSettingsCard({
  task,
  title,
  meta,
}: VisionTaskSettingsCardProps) {
  const fieldPrefix = task === "recognize" ? "识别" : "描述";
  const thinkingField = `${task}_thinking_mode` as const;
  const effortField = `${task}_reasoning_effort` as const;
  const temperatureField = `${task}_temperature` as const;
  const maxTokensField = `${task}_max_tokens` as const;
  const timeoutField = `${task}_timeout_seconds` as const;

  return (
    <Card className="vision-task-card" size="small" title={title}>
      <Row gutter={[16, 0]}>
        <Col xs={24} md={12}>
          <Form.Item
            name={["vision", thinkingField]}
            label={`${fieldPrefix}思考模式`}
            rules={[{ required: true, message: `请选择${fieldPrefix}思考模式` }]}
            extra={meta(thinkingField, "服务商默认表示不发送显式开关。")}
          >
            <Select options={thinkingModeOptions} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12}>
          <Form.Item
            name={["vision", effortField]}
            label={`${fieldPrefix}思考强度`}
            rules={[{ required: true, message: `请选择${fieldPrefix}思考强度` }]}
            extra={meta(effortField, "是否生效由上游模型决定，保持通用可编辑。")}
          >
            <Select options={reasoningEffortOptions} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12}>
          <Form.Item
            name={["vision", temperatureField]}
            label={`${fieldPrefix} Temperature`}
            rules={[{ required: true, message: `请输入${fieldPrefix} Temperature` }]}
            extra={meta(temperatureField, "范围 0–2；不会因思考模式而禁用。")}
          >
            <InputNumber min={0} max={2} step={0.1} precision={2} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12}>
          <Form.Item
            name={["vision", maxTokensField]}
            label={`${fieldPrefix}最大完成 Tokens`}
            rules={[{ required: true, message: `请输入${fieldPrefix}最大完成 Tokens` }]}
            extra={meta(
              maxTokensField,
              `范围 1–32768；默认 ${task === "recognize" ? 4000 : 800}。`,
            )}
          >
            <InputNumber min={1} max={32768} precision={0} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12}>
          <Form.Item
            name={["vision", timeoutField]}
            label={`${fieldPrefix}请求超时（秒）`}
            rules={[{ required: true, message: `请输入${fieldPrefix}请求超时` }]}
            extra={meta(timeoutField, "范围 1–600 秒。")}
          >
            <InputNumber min={1} max={600} step={1} />
          </Form.Item>
        </Col>
      </Row>
    </Card>
  );
}

export function VisionSettingsCard({
  form,
  settings,
  apiKeyIntent,
  testing,
  llmDirty,
  onApiKeyIntentChange,
  onTestConnection,
}: VisionSettingsCardProps) {
  const [testTask, setTestTask] = useState<VisionTestTask>("recognize");
  const enabled = Form.useWatch(["vision", "enabled"], form) ?? settings.vision.enabled;
  const source =
    (Form.useWatch(["vision", "source"], form) as VisionSource | undefined) ??
    settings.vision.source;
  const customSource = source === "custom";
  const keyStatus = settings.vision.api_key;
  const effectiveConnection = source === "follow_llm" ? settings.llm : settings.vision.effective_connection;
  const keyPlaceholder = keyStatus.configured
    ? `已配置（${keySourceLabel(keyStatus.source)}${
        keyStatus.masked_suffix ? `，尾号 ${keyStatus.masked_suffix}` : ""
      }）`
    : "尚未配置自定义视觉 API Key";

  const meta = (field: string, note?: string, inactive = false) => (
    <FieldMeta
      source={settings.field_sources[`vision.${field}`]}
      activation={settings.field_effects[`vision.${field}`]}
      note={note}
      inactive={inactive}
    />
  );

  const advanced = (
    <Row gutter={[20, 0]}>
      <Col xs={24} md={12} lg={8}>
        <Form.Item
          name={["vision", "max_image_edge"]}
          label="最大图像边长（px）"
          rules={[{ required: true }]}
          extra={meta("max_image_edge", "范围 256–4096；上传前按最长边缩放。")}
        >
          <InputNumber min={256} max={4096} precision={0} />
        </Form.Item>
      </Col>
      <Col xs={24} md={12} lg={8}>
        <Form.Item
          name={["vision", "max_calls_per_document"]}
          label="单文档最大视觉调用次数"
          rules={[{ required: true }]}
          extra={meta("max_calls_per_document", "范围 1–500；达到预算后跳过后续视觉调用。")}
        >
          <InputNumber min={1} max={500} precision={0} />
        </Form.Item>
      </Col>
    </Row>
  );

  return (
    <Card
      className="settings-card vision-settings-card"
      title="视觉模型"
      extra={
        <Space size={6} wrap>
          <Tag color={enabled ? "green" : "default"}>
            {enabled ? "视觉处理已启用" : "视觉处理已关闭"}
          </Tag>
          <Tag>{sourceLabel(source)}</Tag>
        </Space>
      }
    >
      <Row gutter={[20, 0]}>
        <Col xs={24} md={12} lg={8}>
          <Form.Item
            name={["vision", "enabled"]}
            label="启用视觉处理"
            valuePropName="checked"
            extra={meta("enabled", "关闭后不在文档上传流程中调用视觉模型。")}
          >
            <Switch aria-label="启用视觉处理" checkedChildren="启用" unCheckedChildren="关闭" />
          </Form.Item>
        </Col>
        <Col xs={24} md={12} lg={8}>
          <Form.Item
            name={["vision", "source"]}
            label="连接来源"
            rules={[{ required: true, message: "请选择视觉连接来源" }]}
            extra={meta("source", "跟随模式共用问答连接，识别参数和启用开关独立。")}
          >
            <Select
              aria-label="视觉连接来源"
              options={[
                { value: "follow_llm", label: "跟随问答模型" },
                { value: "custom", label: "自定义视觉模型" },
              ]}
            />
          </Form.Item>
        </Col>
        <Col xs={24} md={12} lg={8} style={{ display: customSource ? undefined : "none" }}>
          <Form.Item
            name={["vision", "provider"]}
            label="视觉服务商"
            rules={[{ required: true, message: "请选择视觉服务商" }]}
            extra={meta("provider", "选择实际接口类型，用于发送相应的思考参数。", !customSource)}
          >
            <Select disabled={!customSource} options={providerOptions} />
          </Form.Item>
        </Col>
      </Row>

      <Row gutter={[20, 0]} style={{ display: customSource ? undefined : "none" }}>
        <Col xs={24} lg={14}>
          <Form.Item
            name={["vision", "base_url"]}
            label="视觉 Base URL"
            rules={
              customSource && enabled
                ? [
                    { required: true, whitespace: true, message: "请输入视觉 Base URL" },
                    { type: "url", message: "请输入有效的 HTTP(S) 地址" },
                  ]
                : []
            }
            extra={meta(
              "base_url",
              customSource && enabled
                ? "自定义且启用时必须填写有效的 HTTP(S) 地址。"
                : "跟随模式或关闭视觉处理时允许为空。",
              !customSource,
            )}
          >
            <Input
              aria-label="视觉 Base URL"
              disabled={!customSource}
              placeholder="https://api.example.com/v1"
              autoComplete="url"
            />
          </Form.Item>
        </Col>
        <Col xs={24} lg={10}>
          <Form.Item
            name={["vision", "model"]}
            label="视觉模型名"
            rules={[
              { required: true, whitespace: true, message: "请输入视觉模型名" },
              { max: 200, message: "视觉模型名不能超过 200 个字符" },
            ]}
            extra={meta("model", "请填写该接口支持图片输入的模型名。", !customSource)}
          >
            <Input
              aria-label="视觉模型名"
              disabled={!customSource}
              placeholder="例如 glm-4.6v-flash"
              autoComplete="off"
            />
          </Form.Item>
        </Col>
      </Row>

      <Row gutter={[20, 0]}>
        <Col xs={24} lg={14}>
          <Alert
            className="vision-effective-connection"
            type={source === "follow_llm" ? "info" : "success"}
            showIcon
            title={
              source === "follow_llm"
                ? "跟随问答模型 · 当前已保存的有效连接"
                : "当前已保存的视觉有效连接"
            }
            description={
              <Space orientation="vertical" size={4}>
                <Space size={[6, 4]} wrap>
                  <Tag>{providerLabel(effectiveConnection.provider)}</Tag>
                  <Typography.Text code>{effectiveConnection.model}</Typography.Text>
                  <Typography.Text type="secondary">
                    {effectiveConnection.base_url || "Base URL 未配置"}
                  </Typography.Text>
                </Space>
                <Typography.Text type="secondary">
                  {keyStatusLabel(effectiveConnection.api_key)}
                </Typography.Text>
                <Typography.Text type="secondary">
                  {source === "follow_llm"
                    ? "连接测试会使用这份已保存的问答模型配置。"
                    : "修改自定义连接后，保存才会更新有效连接。"}
                </Typography.Text>
              </Space>
            }
          />
        </Col>
        <Col xs={24} lg={10} style={{ display: customSource ? undefined : "none" }}>
          <Form.Item
            name={["vision", "api_key_draft"]}
            label="自定义视觉 API Key"
            extra={
              <Space orientation="vertical" size={4}>
                <FieldMeta
                  source={settings.field_sources["vision.api_key"]}
                  activation={settings.field_effects["vision.api_key"]}
                  note="输入框永不回填旧密钥；留空表示保留当前密钥。"
                />
                <Typography.Text type="secondary">
                  {source === "follow_llm"
                    ? "当前跟随问答模型；此密钥仅在切换到自定义时使用。"
                    : "保存时独立更新自定义视觉密钥。"}
                </Typography.Text>
                {apiKeyIntent === "clear" && (
                  <Typography.Text type="danger">
                    保存时将清除运行时视觉密钥覆盖；若环境变量仍有 Key，将恢复使用环境变量。
                  </Typography.Text>
                )}
              </Space>
            }
          >
            <Input.Password
              aria-label="视觉 API Key"
              placeholder={keyPlaceholder}
              autoComplete="new-password"
              onChange={(event) =>
                onApiKeyIntentChange(event.target.value ? "replace" : "keep")
              }
            />
          </Form.Item>
          <Popconfirm
            title="确认清除自定义视觉 API Key？"
            description="保存后将删除页面视觉密钥；环境变量 Key 不会被删除。"
            okText="确认清除"
            cancelText="取消"
            onConfirm={() => {
              form.setFieldValue(["vision", "api_key_draft"], "");
              onApiKeyIntentChange("clear");
            }}
          >
            <Button
              danger
              size="small"
              icon={<DeleteOutlined />}
              disabled={!keyStatus.configured || apiKeyIntent === "clear"}
            >
              清除已保存视觉密钥
            </Button>
          </Popconfirm>
        </Col>
      </Row>

      <Divider titlePlacement="start">文档处理场景</Divider>
      <Row gutter={[20, 0]}>
        <Col xs={24} md={12} lg={8}>
          <Form.Item
            name={["vision", "pdf_scanned_enabled"]}
            label="处理扫描 PDF"
            valuePropName="checked"
            extra={meta("pdf_scanned_enabled", "对扫描页或缺少可用文本层的 PDF 进行视觉识别。")}
          >
            <Switch aria-label="处理扫描 PDF" checkedChildren="启用" unCheckedChildren="关闭" />
          </Form.Item>
        </Col>
        <Col xs={24} md={12} lg={8}>
          <Form.Item
            name={["vision", "pdf_page_images_enabled"]}
            label="PDF 图片／图表描述"
            valuePropName="checked"
            extra={meta("pdf_page_images_enabled", "为有原生文字且包含图片的 PDF 页面补充图表描述。")}
          >
            <Switch aria-label="PDF 图片／图表描述" checkedChildren="启用" unCheckedChildren="关闭" />
          </Form.Item>
        </Col>
        <Col xs={24} md={12} lg={8}>
          <Form.Item
            name={["vision", "embedded_images_enabled"]}
            label="处理文档内嵌图片"
            valuePropName="checked"
            extra={meta("embedded_images_enabled", "识别 DOCX、PPTX 等文档中的内嵌图片。")}
          >
            <Switch aria-label="处理文档内嵌图片" checkedChildren="启用" unCheckedChildren="关闭" />
          </Form.Item>
        </Col>
      </Row>

      <Divider titlePlacement="start">识别与描述参数</Divider>
      <Alert
        type="info"
        showIcon
        title="思考参数保持通用且始终可编辑"
        description="不同服务商或模型可能忽略、不支持或重新解释思考强度与 Temperature；页面不会因为某个服务商而隐藏或锁定这些字段。"
      />
      <Row gutter={[20, 16]} className="vision-task-grid">
        <Col xs={24} xl={12}>
          <VisionTaskSettingsCard
            task="recognize"
            title="图片识别"
            meta={meta}
          />
        </Col>
        <Col xs={24} xl={12}>
          <VisionTaskSettingsCard
            task="describe"
            title="图片描述"
            meta={meta}
          />
        </Col>
      </Row>

      <Collapse
        ghost
        items={[
          {
            key: "advanced",
            label: "高级视觉参数与调用预算",
            children: advanced,
            forceRender: true,
          },
        ]}
      />

      <div className="vision-test-bar">
        {source === "follow_llm" && llmDirty && (
          <Alert
            className="vision-test-warning"
            type="warning"
            showIcon
            title="问答模型有未保存修改"
            description="本次视觉测试仍使用当前已保存的 LLM 连接，不会使用未保存的问答模型草稿。"
          />
        )}
        <Space wrap align="center">
          <Typography.Text strong>测试任务</Typography.Text>
          <Radio.Group
            aria-label="测试任务"
            optionType="button"
            buttonStyle="solid"
            value={testTask}
            onChange={(event) => setTestTask(event.target.value as VisionTestTask)}
          >
            <Radio.Button value="recognize">识别</Radio.Button>
            <Radio.Button value="describe">描述</Radio.Button>
          </Radio.Group>
          <Button
            icon={<ApiOutlined />}
            loading={testing}
            onClick={() => onTestConnection(testTask)}
          >
            测试视觉连接
          </Button>
          <Typography.Text type="secondary">
            使用所选任务的当前草稿发起一次最小真实请求，可能产生少量费用。
          </Typography.Text>
        </Space>
      </div>
    </Card>
  );
}
