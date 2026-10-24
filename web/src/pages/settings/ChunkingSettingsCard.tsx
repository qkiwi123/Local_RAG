import { Alert, Card, Col, Form, InputNumber, Row, Select, type FormInstance } from "antd";
import { ScissorOutlined } from "@ant-design/icons";
import type { SettingsReadResponse } from "../../api/types";
import type { SettingsFormValues } from "./formTypes";
import { FieldMeta } from "./FieldMeta";

interface ChunkingSettingsCardProps {
  form: FormInstance<SettingsFormValues>;
  settings: SettingsReadResponse;
}

export function ChunkingSettingsCard({ form, settings }: ChunkingSettingsCardProps) {
  const draft = Form.useWatch("chunking", form) ?? settings.chunking;
  const recursive = draft.strategy === "recursive";
  const meta = (field: string, note: string, inactive = false) => (
    <FieldMeta source={settings.field_sources[`chunking.${field}`]}
      activation={settings.field_effects[`chunking.${field}`]} note={note} inactive={inactive} />
  );
  const positiveInteger = [
    { required: true, message: "请输入数值" },
    { type: "integer" as const, min: 1, message: "请输入大于 0 的整数" },
  ];

  return (
    <Card className="settings-card" title={<span className="settings-card-title"><ScissorOutlined />文档分块</span>}
      extra={<span className="settings-card-caption">下一次上传生效</span>}>
      <p className="settings-card-intro">上传时，将文档切成便于检索的片段。长度控制每段大小，重叠保留相邻片段之间的上下文。</p>
      <Row gutter={[20, 0]}>
        <Col xs={24} md={12}>
          <Form.Item name={["chunking", "strategy"]} label="切分方式" rules={[{ required: true, message: "请选择切分方式" }]}
            extra={meta("strategy", recursive ? "优先按段落、句子边界切分，并检查 Token 上限。" : "按固定字符长度切分，兼容原有方式。") }>
            <Select options={[
              { value: "recursive", label: "按语义边界切分 · 递归" },
              { value: "fixed", label: "按固定长度切分" },
            ]} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12}>
          <Form.Item name={["chunking", "chunk_size"]} label="分块长度（字符）" rules={positiveInteger}
            extra={meta("chunk_size", "每个片段的目标字符数，含标点；实际长度可能更短。") }>
            <InputNumber min={1} precision={0} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12}>
          <Form.Item name={["chunking", "chunk_overlap"]} label="重叠长度（字符）"
            dependencies={[["chunking", "chunk_size"]]}
            rules={[
              { required: true, message: "请输入重叠长度" },
              { type: "integer", min: 0, message: "请输入不小于 0 的整数" },
              ({ getFieldValue }) => ({ validator(_, value?: number) {
                return value === undefined || value === null || value < getFieldValue(["chunking", "chunk_size"])
                  ? Promise.resolve() : Promise.reject(new Error("重叠长度必须小于分块长度"));
              } }),
            ]}
            extra={meta("chunk_overlap", "相邻片段重复保留的字符数；0 表示不重叠，必须小于分块长度。") }>
            <InputNumber min={0} precision={0} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12}>
          <Form.Item name={["chunking", "max_tokens"]} label="单片段 Token 上限" rules={positiveInteger}
            extra={meta("max_tokens", recursive ? "按本地向量模型的分词器计数，与字符数不同。" : "仅递归切分使用；切换后可调整，当前值会保留。", !recursive) }>
            <InputNumber min={1} precision={0} disabled={!recursive} />
          </Form.Item>
        </Col>
      </Row>
      <Alert type="info" showIcon title="保存后用于下一次上传"
        description="已有文档会保留原来的分块。若要应用新参数，请重新上传对应文档；保存设置不会自动重新切分或重建已有文档。" />
    </Card>
  );
}
