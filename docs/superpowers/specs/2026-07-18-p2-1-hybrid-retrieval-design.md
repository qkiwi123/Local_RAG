# P2-1 Dense + BM25 + RRF 混合检索设计

> 状态：已由用户于 2026-07-18 确认。实现目标来自 `RAG优化方案.md` 的 P2-1，当前基线来自 `docs/交接_阶段一优化.md`。

## 1. 背景与目标

当前生产链路为 Dense top 15 → distance ≤ 0.67 → `bge-reranker-v2-m3` → 每文档最多 3 条 → top 5。P1-5 在原 21 题上已达到 Hit Rate 100%、Recall 94.12%、MRR 97.06%，但多文档 Recall 仍为 66.67%，且现有题集不足以单独观察数字、型号、缩写和专有名词的词法检索价值。

P2-1 的目标是在不迁移 Chroma、不修改 Embedding、切分、阈值、Reranker 和 LLM 的前提下，增加应用侧中文 BM25，并以 RRF 融合 Dense/BM25 排名。最终是否部署由同数据集消融结果决定，不因“技术栈更完整”而预设混合检索一定更好。

## 2. 方案选择

采用 `jieba==0.42.1` + `rank-bm25==0.2.2`：

- `jieba` 负责中文精确模式分词，并保留英文、数字、型号和缩写 token；
- `rank-bm25` 提供经过验证的 BM25Okapi 实现，避免在本阶段自行实现打分公式；
- 不采用字符 n-gram，原因是其不符合优化方案对中文分词的要求，且面试解释性较弱；
- 不自行实现 BM25，原因是本阶段要验证混合检索收益，而不是展示检索公式实现。

## 3. 查询链路

```text
用户问题
  ├── BGE Dense Top N ── distance <= 0.67
  └── jieba tokenize ── BM25 Top N（只保留 score > 0）
                    ↓
               RRF 按排名融合
                    ↓
             融合候选截断到 15
                    ↓
          bge-reranker-v2-m3 精排
                    ↓
            每文档最多 3 条 → Top 5
```

RRF 使用 `1 / (rrf_k + rank)`，默认 `rrf_k=60`。Dense distance、BM25 score 和 Reranker score 语义不同，禁止直接归一化后相加。RRF 只使用名次，同一 chunk 通过稳定 `chunk_id` 去重。

## 4. 模块边界

### 4.1 `backend/services/sparse_retriever.py`

- `tokenize(text) -> list[str]`：NFKC 规范化、转小写、jieba 精确分词、去除纯空白和纯标点；
- 惰性读取 Chroma 全量 chunk 并构建 `BM25Okapi`；
- `search(query, top_k) -> list[dict]`：只返回 BM25 score 大于 0 的结果；
- 输出保留 `chunk_id/content/metadata`，新增 `bm25_rank/bm25_score`，Sparse-only 结果的 `distance` 为 `None`；
- 使用线程锁保护首次构建和失效后的重建。

### 4.2 `backend/services/fusion.py`

- 提供无外部状态的 `reciprocal_rank_fusion(dense, sparse, rrf_k, top_k)`；
- 输出每个候选的 `rrf_score/dense_rank/bm25_rank`；
- Dense-only 与 Sparse-only 候选均可进入融合；
- 相同 RRF 分数时依次按最佳分支名次、`chunk_id` 做确定性排序。

### 4.3 `backend/services/vector_store.py`

- 新增 `get_all_chunks()`，只读取 id、documents、metadatas，不读取 embedding；
- 新增进程内 revision 计数；成功 add/delete 后递增；
- Sparse 索引以 revision 判断是否重建；后端重启后首次查询从当前 Chroma 快照构建。

项目当前是单后端进程、本地 Chroma Demo，因此不新增 BM25 持久化文件。跨进程同时修改 Chroma 不属于 P2-1 范围；未来迁移 Milvus 时再由统一索引任务替代进程内 revision。

### 4.4 `backend/services/retriever.py`

增加三种检索模式：

- `dense`：保持 P1-4/P1-5 行为；
- `bm25`：只执行 Sparse 检索；
- `hybrid`：并行语义上的 Dense/BM25 双路检索后执行 RRF；本阶段不引入线程池，避免小语料下增加复杂度。

Reranker 开关独立于检索模式。开启时，三种模式都先产生候选 15，再精排并截取 top 5；关闭时保留现有 `top_k * 2` 候选策略。每文档最多 3 条仍在 Reranker 之后执行。

## 5. 配置

新增：

```dotenv
RETRIEVAL_MODE=dense
BM25_TOP_K=15
BM25_K1=1.5
BM25_B=0.75
RRF_K=60
```

代码默认 `dense`，保证缺少新依赖或未明确启用时不改变现有行为。实验通过后只把生产 `.env` 和 `.env.example` 切换为 `hybrid`。配置快照和 eval CLI 必须记录/覆盖这些参数。

## 6. 结果契约与错误处理

统一检索结果字段：

```text
chunk_id, content, metadata,
distance?, dense_rank?, bm25_score?, bm25_rank?, rrf_score?, reranker_score?
```

其中问号字段允许为 `None`。报告生成器不得假设所有结果都有 Dense distance。

- 空知识库、空查询或 BM25 全部为 0 分时返回空列表；
- 非法 `RETRIEVAL_MODE`、非正数 top-k、BM25 参数和 RRF 参数在配置校验阶段报错；
- Dense 阈值只过滤 Dense 分支，不把阈值错误套到 BM25 score；
- BM25 不设经验阈值，只过滤 score=0，域外误召回由消融题和拒答指标验证；
- Reranker/Embedding 仍只从本地模型快照加载。

## 7. 评测设计

新增独立 dataset v2，保留原 q01-q21，并增加 6 道不参与前序调参的 P2-1 题：

- 精确型号/框架：Claude Code、AutoGPT、CrewAI；
- 缩写：APE；
- 向量数据库产品名：Pinecone、Weaviate、Chroma、Qdrant、Milvus；
- 访问控制缩写：RBAC；
- 精确比例：30-50%；
- 域内数字陷阱：Qwen 2.5 的具体基准分数（语料无答案，应拒答）。

新增 `hybrid-ablation` 命令，一次 parse/chunk/embed 后依次运行：

1. Dense；
2. BM25；
3. Hybrid；
4. Hybrid + Reranker。

每组输出逐题 JSONL，并汇总 Hit Rate、Recall、MRR、原 21 题回归、新增题指标、不可回答题进入 LLM 比例、mean/P50/P95 检索延迟和改善/回归题。

## 8. 部署门槛

只有同时满足以下条件才把生产模式切换为 `hybrid`：

1. 在相同 dataset v2 上，相比 Dense + Reranker，Hit Rate、Recall、MRR 至少一项明确提升；
2. 原 q01-q21 无明显检索或回答回归；
3. 新增精确匹配题能给出可解释的 BM25/RRF 排名收益；
4. 不可回答题的拒答准确率不下降，伪引用仍为 0；
5. 记录 Reranker CPU 延迟，P95 变化可解释。

若不满足，则保留 `dense + reranker` 生产配置，提交代码、消融报告和负实验结论，但不宣称混合检索带来质量提升。

## 9. 测试范围

- 分词：中文、英文大小写、型号、数字、空白和标点；
- BM25：精确词排名、0 分过滤、top-k、空库、索引失效重建；
- RRF：去重、双路/单路候选、排名计算、确定性排序；
- Retriever：三种模式、Dense 阈值、Reranker 前后顺序、每文档上限；
- Eval：配置快照、CLI 覆盖、四组消融、配置恢复、Sparse-only 报告；
- 集成：真实 Chroma add/delete 后 BM25 可见性；
- 回归：全量 pytest、CI 子集、正式离线消融、必要时真实 DeepSeek 与生产 HTTP 冒烟。
