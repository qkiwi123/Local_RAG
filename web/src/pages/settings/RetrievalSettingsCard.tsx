import {
  Alert, Card, Col, Collapse, Form, InputNumber, Row, Select, Switch,
  type FormInstance,
} from "antd";
import { FilterOutlined } from "@ant-design/icons";
import type { SettingsReadResponse } from "../../api/types";
import type { SettingsFormValues } from "./formTypes";
import { FieldMeta } from "./FieldMeta";

interface RetrievalSettingsCardProps {
  form: FormInstance<SettingsFormValues>;
  settings: SettingsReadResponse;
}

export function RetrievalSettingsCard({ form, settings }: RetrievalSettingsCardProps) {
  const draft = Form.useWatch("retrieval", form) ?? settings.retrieval;
  const mode = draft.mode;
  const meta = (field: string, note?: string, inactive = false) => (
    <FieldMeta source={settings.field_sources[`retrieval.${field}`]}
      activation={settings.field_effects[`retrieval.${field}`]} note={note} inactive={inactive} />
  );
  const atLeastFinalTopK = ({ getFieldValue }: { getFieldValue: (name: (string | number)[]) => number }) => ({
    validator(_: unknown, value?: number) {
      return value !== undefined && value >= getFieldValue(["retrieval", "final_top_k"])
        ? Promise.resolve() : Promise.reject(new Error("不能小于最终 Top K"));
    },
  });
  const candidateRules = [{ required: true }, { type: "integer" as const, min: 1, max: 100, message: "请输入 1–100 的整数" }, atLeastFinalTopK];

  return (
    <Card className="settings-card settings-retrieval-card" title={<span className="settings-card-title"><FilterOutlined />检索设置</span>} extra={<span className="settings-card-caption">下一次提问生效</span>}>
      <p className="settings-card-intro">先找到相关片段，再排序、筛选，组成回答依据。召回数量与最终回答使用的数量可以分别调整。</p>
      <div className="settings-section-heading"><h3>召回设置</h3><p>控制从知识库中找出多少候选片段</p></div>
      <Row gutter={[20, 0]}>
        <Col xs={24} md={12} xl={8}>
          <Form.Item name={["retrieval", "mode"]} label="检索模式" rules={[{ required: true, message: "请选择检索模式" }]} extra={meta("mode")}>
            <Select options={[{ value: "dense", label: "Dense · 语义检索" }, { value: "bm25", label: "BM25 · 关键词检索" }, { value: "hybrid", label: "Hybrid · 混合检索" }]} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12} xl={8}>
          <Form.Item name={["retrieval", "dense_candidate_k"]} label="Dense 召回 Top K" dependencies={[["retrieval", "final_top_k"]]} rules={candidateRules} extra={meta("dense_candidate_k", "语义召回数量，1–100。", mode === "bm25")}>
            <InputNumber min={1} max={100} precision={0} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12} xl={8}>
          <Form.Item name={["retrieval", "bm25_top_k"]} label="BM25 召回 Top K" dependencies={[["retrieval", "final_top_k"]]} rules={candidateRules} extra={meta("bm25_top_k", "关键词召回数量，1–100。", mode === "dense")}>
            <InputNumber min={1} max={100} precision={0} />
          </Form.Item>
        </Col>
      </Row>
      <div className="settings-preview-label">配置预览 · 保存后生效</div>
      <div className="retrieval-pipeline" aria-label="当前表单的检索流程">
        <div><span>01 / 召回</span><strong>{mode === "dense" ? "语义检索" : mode === "bm25" ? "关键词检索" : "语义 + 关键词"}</strong><small>{mode !== "bm25" && `Dense ${draft.dense_candidate_k} 条`}{mode === "hybrid" && " · "}{mode !== "dense" && `BM25 ${draft.bm25_top_k} 条`}</small></div>
        <div><span>02 / 排序</span><strong>{draft.reranker_enabled ? "模型重排" : mode === "hybrid" ? "RRF 融合排序" : "原始检索排序"}</strong><small>{draft.reranker_enabled ? `最多 ${draft.reranker_candidate_k} 条进入重排` : "未启用重排模型"}</small></div>
        <div><span>03 / 回答依据</span><strong>最多 {draft.final_top_k} 个片段</strong><small>每份文档最多 {draft.max_chunks_per_document} 个</small></div>
      </div>
      <div className="settings-section-heading"><h3>重排与回答上下文</h3><p>缩小候选范围，避免同一份文档占满回答依据</p></div>
      <Row gutter={[20, 0]}>
        <Col xs={24} md={12} xl={8}>
          <Form.Item name={["retrieval", "reranker_enabled"]} label="启用 Reranker" valuePropName="checked" extra={meta("reranker_enabled", "用本地模型再次判断候选片段的相关性。")}>
            <Switch checkedChildren="启用" unCheckedChildren="关闭" />
          </Form.Item>
        </Col>
        <Col xs={24} md={12} xl={8}>
          <Form.Item name={["retrieval", "reranker_candidate_k"]} label="进入重排的候选数" dependencies={[["retrieval", "final_top_k"]]} rules={candidateRules} extra={meta("reranker_candidate_k", "候选上限，1–100。", !draft.reranker_enabled)}>
            <InputNumber min={1} max={100} precision={0} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12} xl={8}>
          <Form.Item name={["retrieval", "final_top_k"]} label="最终 Top K" rules={[{ required: true }, { type: "integer", min: 1, max: 20 }]} extra={meta("final_top_k", "最多送入回答的片段数，1–20。")}>
            <InputNumber min={1} max={20} precision={0} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12} xl={8}>
          <Form.Item name={["retrieval", "max_chunks_per_document"]} label="单文档最大片段数" dependencies={[["retrieval", "final_top_k"]]} rules={[{ required: true }, ({ getFieldValue }) => ({ validator(_, value?: number) { return value !== undefined && value <= getFieldValue(["retrieval", "final_top_k"]) ? Promise.resolve() : Promise.reject(new Error("不能大于最终 Top K")); } })]} extra={meta("max_chunks_per_document", "不能超过最终 Top K。")}>
            <InputNumber min={1} max={20} precision={0} />
          </Form.Item>
        </Col>
      </Row>
      <Collapse ghost items={[{ key: "advanced", label: "高级检索参数 · 权重与阈值", forceRender: true, children: <>
        <Row gutter={[20, 0]}>
          <Col xs={24} md={12}><Form.Item name={["retrieval", "bm25_k1"]} label="BM25 k1" rules={[{ required: true }, { type: "number", min: 0.01 }]} extra={meta("bm25_k1", "控制词频的影响程度。", mode === "dense")}><InputNumber min={0.01} step={0.1} /></Form.Item></Col>
          <Col xs={24} md={12}><Form.Item name={["retrieval", "bm25_b"]} label="BM25 b" rules={[{ required: true }]} extra={meta("bm25_b", "文档长度归一化权重，0–1。", mode === "dense")}><InputNumber min={0} max={1} step={0.05} /></Form.Item></Col>
          <Col xs={24} md={12}><Form.Item name={["retrieval", "rrf_k"]} label="RRF k" rules={[{ required: true }]} extra={meta("rrf_k", "混合检索的排名融合常数，1–200。", mode !== "hybrid")}><InputNumber min={1} max={200} precision={0} /></Form.Item></Col>
          <Col xs={24} md={12}><Form.Item name={["retrieval", "dense_distance_threshold"]} label="Dense 距离上限" rules={[{ required: true }]} extra={meta("dense_distance_threshold", "0–2，越小越严格；只过滤语义检索分支。", mode === "bm25")}><InputNumber min={0} max={2} step={0.01} /></Form.Item></Col>
        </Row>
        <Alert type="info" showIcon title="当前策略：不启用硬拒答门控" description="证据不足时由回答规则约束回复，仍展示本轮检索来源。Dense 距离上限并非混合检索的通用拒答阈值。" />
      </> }]} />
    </Card>
  );
}
