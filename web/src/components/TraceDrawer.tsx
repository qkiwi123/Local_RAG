import { Collapse, Drawer, Progress, Table, Tag, Typography } from "antd";
import type { RetrievalTrace } from "../api/types";

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
  const knownSum = Object.keys(TIMING_LABELS).reduce(
    (sum, key) => sum + (Number(timings[key]) || 0),
    0,
  );
  const total = Number(timings.total) || 0;
  const other = Math.max(0, total - knownSum);

  return (
    <Drawer
      open
      title={`🔎 检索详情 · ${trace.retrieval_mode}${trace.reranker_enabled ? " + Reranker" : ""}`}
      onClose={onClose}
      width={860}
    >
      <Typography.Paragraph>
        最终上下文 {trace.context.chunk_count} chunks · {trace.context.token_count} tokens
        {trace.tag_filter.length > 0 && (
          <>
            {" "}· 标签筛选 {trace.tag_filter.join("、")}
          </>
        )}
      </Typography.Paragraph>

      <Typography.Title level={5}>阶段耗时</Typography.Title>
      {Object.entries(TIMING_LABELS).map(([key, label]) => {
        const value = Number(timings[key]) || 0;
        if (value <= 0) return null;
        return (
          <Progress
            key={key}
            percent={(value / Math.max(total, 1)) * 100}
            format={() => `${label} · ${value.toFixed(1)} ms`}
          />
        );
      })}
      {other > 0 && (
        <Progress
          percent={(other / Math.max(total, 1)) * 100}
          format={() => `其他阶段（追问改写/历史/引用提取/持久化等） · ${other.toFixed(1)} ms`}
        />
      )}
      <Progress
        percent={100}
        strokeColor="#1677ff"
        format={() => `总耗时 · ${total.toFixed(1)} ms`}
      />

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
    </Drawer>
  );
}
