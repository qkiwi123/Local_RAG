import { Collapse, Descriptions, Progress, Space, Table, Tag, Typography } from "antd";
import type { RetrievalTrace } from "../api/types";
import { ResizableDrawer } from "./ResizableDrawer";

const STAGE_LABELS: Record<string, string> = {
  dense: "Dense",
  bm25: "BM25",
  rrf: "RRF",
  reranker: "Reranker",
  selected: "最终上下文",
};

const TIMING_LABELS: Record<string, string> = {
  query_embedding: "查询向量化",
  dense_retrieval: "Dense 检索",
  sparse_retrieval: "Sparse 检索",
  rrf: "RRF 融合",
  rerank: "Reranker 精排",
  context_formatting: "上下文组装",
  llm_generation: "LLM 生成",
};

export function TraceDrawer({
  trace,
  onClose,
}: {
  trace: RetrievalTrace;
  onClose: () => void;
}) {
  const stages = trace.stages.filter((stage) => STAGE_LABELS[stage.name]);
  const timings = trace.timings_ms ?? {};
  const config = trace.effective_config;
  const knownSum = Object.keys(TIMING_LABELS).reduce(
    (sum, key) => sum + (Number(timings[key]) || 0),
    0,
  );
  const total = Number(timings.total) || 0;
  const other = Math.max(0, total - knownSum);

  return (
    <ResizableDrawer
      rootClassName="trace-drawer"
      open
      title={`🔎 检索详情 · ${trace.retrieval_mode}${trace.reranker_enabled ? " + Reranker" : ""}`}
      onClose={onClose}
      initialSize={860}
      minSize={380}
      storageKey="rag.drawer.trace.width"
      resizeHint="拖动左边界调整检索详情宽度"
    >
      <Typography.Paragraph>
        最终上下文 {trace.context.chunk_count} chunks · {trace.context.token_count} tokens
        {trace.tag_filter.length > 0 && (
          <>
            {" "}· 标签筛选 {trace.tag_filter.join("、")}
          </>
        )}
      </Typography.Paragraph>

      <Typography.Title level={5}>本次配置</Typography.Title>
      <Descriptions
        size="small"
        bordered
        column={{ xs: 1, sm: 2 }}
        items={[
          {
            key: "revision",
            label: "Settings revision",
            children:
              trace.settings_revision === null || trace.settings_revision === undefined
                ? <Typography.Text type="secondary">旧 Trace 未记录</Typography.Text>
                : <Tag color="blue">revision {trace.settings_revision}</Tag>,
          },
          {
            key: "llm",
            label: "LLM",
            children: trace.llm ? (
              <Space wrap size={4}>
                <Tag color={trace.llm.called ? "green" : "default"}>
                  {trace.llm.called ? "已调用" : "未调用"}
                </Tag>
                <span>{trace.llm.provider} / {trace.llm.model}</span>
              </Space>
            ) : (
              <Typography.Text type="secondary">旧 Trace 未记录</Typography.Text>
            ),
          },
          {
            key: "retrieval",
            label: "检索模式 / 最终 Top K",
            children: config
              ? `${config.retrieval_mode} / ${config.final_top_k}`
              : `${trace.retrieval_mode} / 未记录`,
          },
          {
            key: "dense",
            label: "Dense",
            children: config
              ? `candidates ${config.dense_candidate_k} · distance ≤ ${config.dense_distance_threshold}`
              : "旧 Trace 未记录",
          },
          {
            key: "bm25",
            label: "BM25",
            children: config
              ? `top K ${config.bm25_top_k} · k1 ${config.bm25_k1} · b ${config.bm25_b}`
              : "旧 Trace 未记录",
          },
          {
            key: "sparse-backend",
            label: "Sparse 后端 / 状态",
            children: trace.sparse_backend ? (
              <Space wrap size={4}>
                <Tag color={trace.sparse_backend === "bm25s_mmap" ? "purple" : "default"}>
                  {trace.sparse_backend}
                </Tag>
                <Tag color={trace.sparse_index_status === "ready" ? "green" : "orange"}>
                  {trace.sparse_index_status ?? "unknown"}
                </Tag>
                {trace.sparse_index_generation && <span>{trace.sparse_index_generation}</span>}
                {(trace.sparse_index_load_ms ?? 0) > 0 && (
                  <span>加载 {trace.sparse_index_load_ms?.toFixed(1)} ms</span>
                )}
              </Space>
            ) : (
              <Typography.Text type="secondary">本次未执行 Sparse 或旧 Trace 未记录</Typography.Text>
            ),
          },
          {
            key: "rrf",
            label: "RRF k",
            children: config?.rrf_k ?? "旧 Trace 未记录",
          },
          {
            key: "reranker",
            label: "Reranker",
            children: config
              ? `${config.reranker_enabled ? "启用" : "关闭"} · candidates ${config.reranker_candidate_k}`
              : "旧 Trace 未记录",
          },
          {
            key: "selection",
            label: "单文档上限 / 拒答门控",
            children: config
              ? `${config.max_chunks_per_document} / ${config.refusal_gate_enabled ? "启用" : "关闭"}`
              : "旧 Trace 未记录",
          },
        ]}
      />

      <Typography.Title level={5}>阶段耗时</Typography.Title>
      <div className="trace-timing-list">
        {Object.entries(TIMING_LABELS).map(([key, label]) => {
          const value = Number(timings[key]) || 0;
          if (value <= 0) return null;
          return (
            <div className="trace-timing-row" key={key}>
              <div className="trace-timing-row__header">
                <span>{label}</span>
                <span>{value.toFixed(1)} ms</span>
              </div>
              <Progress
                percent={(value / Math.max(total, 1)) * 100}
                showInfo={false}
              />
            </div>
          );
        })}
        {other > 0 && (
          <div className="trace-timing-row">
            <div className="trace-timing-row__header">
              <span>其他阶段（追问改写/历史/引用提取/持久化等）</span>
              <span>{other.toFixed(1)} ms</span>
            </div>
            <Progress
              percent={(other / Math.max(total, 1)) * 100}
              showInfo={false}
            />
          </div>
        )}
        <div className="trace-timing-row trace-timing-row--total">
          <div className="trace-timing-row__header">
            <span>总耗时</span>
            <span>{total.toFixed(1)} ms</span>
          </div>
          <Progress percent={100} strokeColor="#1677ff" showInfo={false} />
        </div>
      </div>

      <Typography.Title level={5} style={{ marginTop: 16 }}>
        候选阶段
      </Typography.Title>
      <Collapse
        items={stages.map((stage) => ({
          key: stage.name,
          label: `${STAGE_LABELS[stage.name]}（${stage.candidates.length}）`,
          children: (
            <Table
              size="small"
              rowKey={(row) => String(row.chunk_id)}
              pagination={false}
              scroll={{ x: 900 }}
              dataSource={stage.candidates as Array<Record<string, unknown>>}
              columns={[
                { title: "排名", dataIndex: "rank", width: 60 },
                {
                  title: "文档",
                  dataIndex: "chunk_id",
                  render: (chunkId: string) =>
                    trace.chunks[chunkId]?.document_name || chunkId,
                },
                {
                  title: "位置",
                  dataIndex: "chunk_id",
                  render: (chunkId: string) =>
                    trace.chunks[chunkId]?.location_label || "位置未知",
                },
                { title: "distance", dataIndex: "distance", width: 100 },
                {
                  title: "过滤状态",
                  dataIndex: "filtered_reason",
                  width: 160,
                  render: (reason: string | null) =>
                    reason ? <Tag color="orange">{reason}</Tag> : <Tag color="green">通过</Tag>,
                },
                {
                  title: "是否入选",
                  dataIndex: "selected",
                  width: 90,
                  render: (selected: boolean) =>
                    selected ? <Tag color="blue">是</Tag> : <Tag>否</Tag>,
                },
                {
                  title: "摘要",
                  dataIndex: "chunk_id",
                  ellipsis: true,
                  render: (chunkId: string) => {
                    const content = trace.chunks[chunkId]?.content ?? "";
                    return content.length > 80 ? `${content.slice(0, 80)}…` : content;
                  },
                },
              ]}
            />
          ),
        }))}
      />
    </ResizableDrawer>
  );
}
