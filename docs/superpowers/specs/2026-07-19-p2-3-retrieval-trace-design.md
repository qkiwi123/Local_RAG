# P2-3 检索过程可视化设计

## 1. 背景与范围

P2-2 Milvus 迁移被有意跳过：P2-1 的 Hybrid + Reranker 在同数据集消融中没有提升
Hit Rate、Recall 或 MRR，并使 q18 完全域外题进入 LLM，不满足“混合检索已经证明有指标
收益”的迁移前提。当前继续使用 Chroma + Dense + Reranker，直接进入 P2-3。

P2-3 的目标是在每条回答下提供默认折叠的 Debug/Trace 面板，展示候选从召回到最终上下文
的完整路径和各阶段耗时。它是可观测性功能，不改变检索排序、回答生成或引用协议；检索质量
指标应与启用前一致。

本阶段不实现 PDF 页面预览、引用高亮或 parser bounding box，这些属于 P2-4。

## 2. 安全边界与配置

新增布尔配置 `RETRIEVAL_TRACE_ENABLED`：

- 代码默认值为 `false`；
- `.env.example` 默认值为 `false`；
- 本机真实验收期间在忽略提交的 `.env` 中设置为 `true`；
- 关闭时 API 返回 `trace: null`，前端不渲染 Trace 入口；
- 关闭时不构造候选原文字典，避免文档内容泄露和不必要的响应体开销。

Trace 开启后会返回未进入最终上下文的候选原文，只适合受信任的本地演示或内部调试环境，
不能对不可信用户开放。

## 3. 后端接口与兼容性

保留现有 `retrieve(question, top_k) -> list[dict]` 契约，避免破坏 eval、引用解析和已有调用方。
新增 `retrieve_with_trace(question, top_k) -> RetrievalResponse`，聊天路由改用新入口：

```python
RetrievalResponse(
    results=[...],
    trace=RetrievalTrace(...) | None,
)
```

`ChatResponse` 新增可选字段 `trace`。原有 `answer` 和 `citations` 字段、空检索固定拒答以及
HTTP 502 错误行为保持不变。

### 3.1 Trace 数据结构

候选原文按 `chunk_id` 去重，只在 `chunks` 字典中出现一次。阶段数组通过 chunk ID 引用：

```json
{
  "retrieval_mode": "dense",
  "reranker_enabled": true,
  "chunks": {
    "doc_p6_c7": {
      "chunk_id": "doc_p6_c7",
      "document_name": "LLM应用指南_RAG测试文档.pdf",
      "page_number": 6,
      "content": "完整 chunk 原文"
    }
  },
  "stages": [
    {
      "name": "dense",
      "candidates": [
        {
          "chunk_id": "doc_p6_c7",
          "rank": 1,
          "distance": 0.5572,
          "bm25_score": null,
          "rrf_score": null,
          "reranker_score": null,
          "selected": true
        }
      ]
    }
  ],
  "selected_chunk_ids": ["doc_p6_c7"],
  "context": {
    "chunk_count": 5,
    "char_count": 3270,
    "token_count": 1842
  },
  "timings_ms": {
    "query_embedding": 64.0,
    "dense_retrieval": 71.0,
    "sparse_retrieval": 0.0,
    "rrf": 0.0,
    "rerank": 7556.0,
    "context_formatting": 1.0,
    "llm_generation": 2037.0,
    "total": 9729.0
  }
}
```

阶段只包含本次实际执行的链路：

- Dense：`dense`，可选 `reranker`，`selected`；
- BM25：`bm25`，可选 `reranker`，`selected`；
- Hybrid：`dense`、`bm25`、`rrf`，可选 `reranker`，`selected`。

候选表同时保留已有原始诊断值。某阶段不适用的分数为 `null`，不混淆 distance、BM25、
RRF 和 Reranker 的不同语义。

### 3.2 计时边界

为拆分 query embedding 与 Chroma 查询，向量存储增加接受预计算向量的查询入口；现有
`search(query)` 保留为兼容包装。Retriever 使用 `perf_counter()` 记录：

1. query embedding；
2. Chroma dense retrieval；
3. BM25 sparse retrieval；
4. RRF；
5. Reranker；
6. context formatting；
7. LLM generation；
8. chat 总耗时。

BM25-only 模式不执行 query embedding，对应耗时为 0。关闭 Reranker 或未执行 Hybrid 时，
对应阶段耗时为 0 且不生成阶段标签。

最终上下文的字符数按格式化后的 context 长度计算；token 数使用当前 Embedding tokenizer
的真实 `count_tokens(context)`，不使用字符数估算。

## 4. 前端交互

每条有 Trace 的助手回答下显示一条默认折叠入口：

```text
🔎 检索详情 · Dense + Reranker · 5 chunks · 8.10s
```

展开后采用“阶段标签”布局：

1. 顶部摘要展示候选规模、最终上下文 chunk/字符/token 和检索/LLM/总耗时；
2. 中部按实际执行链路动态生成 Dense、BM25、RRF、Reranker、最终上下文标签；
3. 每个标签用 Streamlit 原生可选行 dataframe 展示排名、文档、页码、chunk ID、适用分数、
   120 字摘要和是否最终选中；
4. 用户选择表格行后，在表格下方展示该 chunk 完整原文；
5. 底部以阶段名称、毫秒值和相对长度进度条展示耗时瓶颈。

Trace 随助手消息写入 `st.session_state.messages`，历史消息重新渲染时仍可交互。旧消息、
服务端开关关闭或 `trace=null` 时不显示入口。所有 Streamlit widget key 使用消息序号、阶段名
和 chunk ID 组成，避免多轮对话冲突。

## 5. 错误处理

- 空检索返回空 `results`，Trace 仍记录已执行阶段、耗时和零上下文统计；LLM 不执行，
  `llm_generation=0`。
- LLM 调用失败继续返回当前 HTTP 502，不把内部 Trace 塞进错误响应。
- 缺失 metadata 时使用现有安全默认值，不因调试面板导致聊天主链路失败。
- 前端遇到未知阶段或缺失 chunk ID 时跳过不可解析行并显示简短提示，不影响回答正文。
- Trace 构造只复制数据，不修改候选字典，防止影响 Reranker、引用映射或 eval 结果。

## 6. 测试与验收

### 6.1 自动化测试

- 配置：默认关闭、合法布尔值解析、非法值报错；
- 向量存储：预计算 query embedding 查询与现有 search 返回一致；
- Retriever：三种模式的动态阶段、排名和分数映射、chunk 去重、最终选择、空检索；
- API：关闭时 `trace=null`，开启时返回完整 chunk 字典，原 answer/citations 不变；
- 前端：入口默认折叠、动态标签、单行选择显示完整 chunk、历史消息重渲染；
- 回归：全量 pytest 与 CI 子集均通过。

所有生产代码按 RED → GREEN → REFACTOR 的 TDD 顺序实现。

### 6.2 真实环境验收

1. 本机 `.env` 开启 Trace；
2. 使用现有生产索引、`BAAI/bge-large-zh-v1.5`、本地 Reranker 和 DeepSeek 完成至少一道
   真实问答；
3. 在 Streamlit 页面确认面板默认折叠、阶段表、完整 chunk 和耗时展示均可操作；
4. 保存截图和验收记录到 `eval/results/p2-3-retrieval-trace/`；
5. 更新 README 和交接文档，明确 P2-2 的跳过原因、开关风险和使用方式。

P2-3 不以检索质量提升为成功标准。验收重点是 Trace 与实际执行链路一致、关闭时无内容泄露、
开启时可完整解释候选变化，并且不改变现有回答与引用结果。
