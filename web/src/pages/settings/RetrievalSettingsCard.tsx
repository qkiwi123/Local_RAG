import {
  Alert,
  Card,
  Col,
  Collapse,
  Form,
  InputNumber,
  Row,
  Select,
  Switch,
  type FormInstance,
} from "antd";
import type { SettingsReadResponse } from "../../api/types";
import type { SettingsFormValues } from "./formTypes";
import { FieldMeta } from "./FieldMeta";

interface RetrievalSettingsCardProps {
  form: FormInstance<SettingsFormValues>;
  settings: SettingsReadResponse;
}

export function RetrievalSettingsCard({
  form,
  settings,
}: RetrievalSettingsCardProps) {
  const mode = Form.useWatch(["retrieval", "mode"], form);
  const rerankerEnabled = Form.useWatch(
    ["retrieval", "reranker_enabled"],
    form,
  );

  const meta = (field: string, note?: string, inactive = false) => (
    <FieldMeta
      source={settings.field_sources[`retrieval.${field}`]}
      activation={settings.field_effects[`retrieval.${field}`]}
      note={note}
      inactive={inactive}
    />
  );
  const atLeastFinalTopK = ({ getFieldValue }: { getFieldValue: (name: (string | number)[]) => number }) => ({
    validator(_: unknown, value?: number) {
      const finalTopK = getFieldValue(["retrieval", "final_top_k"]);
      return value !== undefined && value >= finalTopK
        ? Promise.resolve()
        : Promise.reject(new Error("不能小于最终 Top K"));
    },
  });

  const advanced = (
    <Row gutter={[20, 0]}>
      <Col xs={24} md={12} lg={8}>
        <Form.Item
          name={["retrieval", "bm25_top_k"]}
          label="BM25 Top K"
          dependencies={[["retrieval", "final_top_k"]]}
          rules={[{ required: true }, atLeastFinalTopK]}
          extra={meta("bm25_top_k", "范围 1–100。", mode === "dense")}
        >
          <InputNumber min={1} max={100} precision={0} />
        </Form.Item>
      </Col>
      <Col xs={24} md={12} lg={8}>
        <Form.Item
          name={["retrieval", "bm25_k1"]}
          label="BM25 k1"
          rules={[{ required: true }, { type: "number", min: 0.01 }]}
          extra={meta("bm25_k1", "修改后刷新内存 BM25 缓存。", mode === "dense")}
        >
          <InputNumber min={0.01} step={0.1} />
        </Form.Item>
      </Col>
      <Col xs={24} md={12} lg={8}>
        <Form.Item
          name={["retrieval", "bm25_b"]}
          label="BM25 b"
          rules={[{ required: true }]}
          extra={meta("bm25_b", "范围 0–1；修改后刷新内存缓存。", mode === "dense")}
        >
          <InputNumber min={0} max={1} step={0.05} />
        </Form.Item>
      </Col>
      <Col xs={24} md={12} lg={8}>
        <Form.Item
          name={["retrieval", "rrf_k"]}
          label="RRF k"
          rules={[{ required: true }]}
          extra={meta("rrf_k", "RRF 排名常数，范围 1–200。", mode !== "hybrid")}
        >
          <InputNumber min={1} max={200} precision={0} />
        </Form.Item>
      </Col>
      <Col xs={24} md={12} lg={8}>
        <Form.Item
          name={["retrieval", "reranker_enabled"]}
          label="启用 Reranker"
          valuePropName="checked"
          extra={meta("reranker_enabled")}
        >
          <Switch checkedChildren="启用" unCheckedChildren="关闭" />
        </Form.Item>
      </Col>
      <Col xs={24} md={12} lg={8}>
        <Form.Item
          name={["retrieval", "reranker_candidate_k"]}
          label="Reranker Candidates"
          dependencies={[["retrieval", "final_top_k"]]}
          rules={[{ required: true }, atLeastFinalTopK]}
          extra={meta(
            "reranker_candidate_k",
            "只控制进入重排器的候选上限。",
            !rerankerEnabled,
          )}
        >
          <InputNumber min={1} max={100} precision={0} />
        </Form.Item>
      </Col>
      <Col span={24}>
        <Form.Item label="不可回答拒答门控">
          <Switch disabled checked={false} />
          <Alert
            className="settings-inline-alert"
            type="info"
            showIcon
            title="当前策略：不启用硬拒答门控"
            description="由回答规则约束证据不足时的回复；无论是否拒答，均展示本轮有效检索来源，区分正文已引用与未引用。Dense distance 不是 Hybrid/BM25 的通用拒答阈值。"
          />
        </Form.Item>
      </Col>
    </Row>
  );

  return (
    <Card className="settings-card" title="检索设置">
      <Row gutter={[20, 0]}>
        <Col xs={24} md={12} lg={8}>
          <Form.Item
            name={["retrieval", "mode"]}
            label="检索模式"
            rules={[{ required: true, message: "请选择检索模式" }]}
            extra={meta("mode")}
          >
            <Select
              options={[
                { value: "dense", label: "Dense" },
                { value: "bm25", label: "BM25" },
                { value: "hybrid", label: "Hybrid" },
              ]}
            />
          </Form.Item>
        </Col>
        <Col xs={24} md={12} lg={8}>
          <Form.Item
            name={["retrieval", "final_top_k"]}
            label="最终 Top K"
            rules={[{ required: true }]}
            extra={meta("final_top_k", "最终送入回答上下文的 chunk 数，范围 1–20。")}
          >
            <InputNumber min={1} max={20} precision={0} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12} lg={8}>
          <Form.Item
            name={["retrieval", "dense_candidate_k"]}
            label="Dense Candidates"
            dependencies={[["retrieval", "final_top_k"]]}
            rules={[{ required: true }, atLeastFinalTopK]}
            extra={meta("dense_candidate_k", "范围最终 Top K–100。", mode === "bm25")}
          >
            <InputNumber min={1} max={100} precision={0} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12} lg={8}>
          <Form.Item
            name={["retrieval", "dense_distance_threshold"]}
            label="Dense 距离上限"
            rules={[{ required: true }]}
            extra={meta(
              "dense_distance_threshold",
              "0–2，越小越严格；只过滤 Dense 分支，不是最终拒答阈值。",
              mode === "bm25",
            )}
          >
            <InputNumber min={0} max={2} step={0.01} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12} lg={8}>
          <Form.Item
            name={["retrieval", "max_chunks_per_document"]}
            label="单文档最大 Chunks"
            dependencies={[["retrieval", "final_top_k"]]}
            rules={[
              { required: true },
              ({ getFieldValue }) => ({
                validator(_, value?: number) {
                  const finalTopK = getFieldValue(["retrieval", "final_top_k"]);
                  return value !== undefined && value <= finalTopK
                    ? Promise.resolve()
                    : Promise.reject(new Error("不能大于最终 Top K"));
                },
              }),
            ]}
            extra={meta("max_chunks_per_document", "避免单份文档占满全部上下文。")}
          >
            <InputNumber min={1} max={20} precision={0} />
          </Form.Item>
        </Col>
      </Row>

      <Collapse
        ghost
        items={[
          {
            key: "advanced",
            label: "高级检索参数",
            children: advanced,
            forceRender: true,
          },
        ]}
      />
    </Card>
  );
}
