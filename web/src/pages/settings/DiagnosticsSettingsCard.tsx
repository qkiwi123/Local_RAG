import { Card, Form, Switch } from "antd";
import { ExperimentOutlined } from "@ant-design/icons";
import type { SettingsReadResponse } from "../../api/types";
import { FieldMeta } from "./FieldMeta";

export function DiagnosticsSettingsCard({ settings }: { settings: SettingsReadResponse }) {
  return <Card className="settings-card" title={<span className="settings-card-title"><ExperimentOutlined />诊断与追踪</span>}
    extra={<span className="settings-card-caption">下一次提问生效</span>}>
    <p className="settings-card-intro">Trace 记录检索的候选片段、筛选过程与各阶段耗时，可从回答下方的“检索详情”查看。</p>
    <Form.Item name={["retrieval", "trace_enabled"]} label="记录检索 Trace" valuePropName="checked"
      extra={<FieldMeta source={settings.field_sources["retrieval.trace_enabled"]} activation={settings.field_effects["retrieval.trace_enabled"]}
        note="关闭后，新提问不再计算或保存 Trace，回答和来源照常显示；历史记录仍可查看。" />}>
      <Switch aria-label="记录检索 Trace" checkedChildren="启用" unCheckedChildren="关闭" />
    </Form.Item>
  </Card>;
}
