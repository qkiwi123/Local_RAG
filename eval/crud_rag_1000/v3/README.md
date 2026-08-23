# CRUD_RAG Dataset v3：120 题 draft 不可回答扩展

本目录是在既有 `../questions_v3.json`、`../corpus/` 和
`../corpus_v3_manifest.json` 基础上的独立版本化产物。原有 100 题、1000
篇文档和 corpus manifest 不被覆盖；新数据集 ID 为 `crud-rag-1000-120q-v2`，复用
不变的 corpus ID `crud-rag-1000-v1`。

## 当前规模

- 题目：120（answerable 100 + unanswerable draft 20）
- 新增来源复杂度：1doc=7、2docs=7、3docs=6
- 新增 split：development=12、validation=4、frozen_test=4
- 不可回答类型：out_of_scope=13、insufficient_detail=7
- corpus：仍为 1000 篇，未复制或修改任何文档
- 审核状态：20 题全部 `draft`，三个 checked 字段全部 `false`

## 确定性构建

从固定 commit `1aace383994e1f68efa12cf2a8e2dadfb4102ceb` 的 `source/split_merged.json` 读取
`questanswer_1doc`、`questanswer_2docs`、`questanswer_3docs`。候选先排除
现有 upstream ID/问题、缺字段和题内重复 gold，再按
`SHA-256(20260819|unanswerable-v3|category|ID|normalized_question)`排序。
每个候选的全部 `news*` 正文必须不在当前 1000 篇 corpus 中；随后执行
规范化正文 hash、5-gram SimHash、长句/120 字符窗口、原答案和数字/日期
关键事实扫描。明确检测到完整答案泄漏的候选会被拒绝。

静态构建产物 `whole_corpus_audit.json` 中的检索槽位按设计保持 `pending`，
以证明静态构建本身没有访问数据库。独立检索阶段另写
`retrieval_top50.json`、`whole_corpus_retrieval_audit.json` 和
`retrieval_summary.md`；应以这些文件判断最近一次检索审计状态。本构建不联网、
不调用 LLM、不加载 embedding/reranker，也不创建数据库。

## 文件

- `questions_v3_120q.json`：运行时数据集；不可回答题不含 evidence/keypoints
  和上游答案。
- `unanswerable_selection_manifest.json`：20 题上游映射、完整 excluded gold
  正文和筛选结果。
- `whole_corpus_audit.json`：逐题自动审计和 pending retrieval 槽位。
- `selection_overrides.json`：语义复核的 hard reject 与 partial support 决策。
- `rejected_review/`：被淘汰候选的跨文档完整答案证据。
- `review/<question-id>.md`：逐题人工审核包。
- `retrieval_top50.json`：独立隔离索引的三路无阈值 Top50 完整结果。
- `whole_corpus_retrieval_audit.json`：逐题检索风险摘要。
- `retrieval_review/`：20 份三路 Top10 语义审核附录。
- `retrieval_summary.md`：检索设备、隔离、耗时和状态摘要。
- `provenance.json`：固定来源、旧产物 hash、新产物 hash 和算法版本。

## 验证

在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  --basetemp eval\crud_rag_1000\.pytest-tmp-unanswerable-v3 `
  eval\crud_rag_1000\test_unanswerable_v3.py
```

测试明确验证 120/1000、7/7/6、12/4/4、旧 100 题和 corpus manifest 未变、
excluded gold 缺失、非严格校验通过以及严格校验因 draft 审核状态失败。

## 120 题 CPU/GPU 检索预基线（2026-08-23）

已在两个全新的隔离目录中分别完成 CPU 与 GPU 的空库入库和 120 题检索，
固定使用 Hybrid、Dense/BM25 候选 15、RRF k=60、Reranker 候选 15、
最终 Top5、Dense 距离阈值 0.67、400/80 recursive chunks。全程启用
Hugging Face/Transformers 离线模式和 retrieval Trace，未调用 LLM/API。

可复现入口：

```powershell
.\.venv\Scripts\python.exe eval\crud_rag_1000\v3\run_retrieval_baseline.py `
  --profile cpu --label <cpu-label>

.\.venv-gpu\Scripts\python.exe eval\crud_rag_1000\v3\run_retrieval_baseline.py `
  --profile gpu --label <gpu-label>
```

本轮产物：

- `runs/cpu-baseline-120q-k15-v3-preflight-20260823/`
- `runs/gpu-baseline-120q-k15-v3-preflight-20260823/`
- `runs/cpu-gpu-baseline-comparison-20260823/`

CPU 与 GPU 各生成 120 行 `retrieval-k15.jsonl`。GPU 运行时确认 Embedding
和 Reranker 实际设备均为 `cuda:0`，CUDA 峰值 allocated/reserved 为
2397.9/2764.0 MiB；最终文档级质量指标与 CPU 一致。完整性能、逐题排序、
阶段 Trace 和生产库哈希隔离证据见对比目录中的 `cpu-gpu-comparison.md/json`。

注意：当前 120 题的 review 仍全部为 `draft`。这些结果只能称为
`draft/preflight` 预基线；完成人工审核前不得称为正式 frozen baseline。
