# CRUD_RAG 1000 文档 / 100 题基线与 120 题 draft 扩展

这是一个固定上游版本的本地 RAG 评测夹具：100 道单轮 QA，语料恰好 1000 篇 UTF-8 `.txt` 文档。构建脚本只依赖本项目现有 Python 环境和 `eval.dataset_v3`，不安装包，也不调用外部模型/API。

## 120 题不可回答扩展

`v3/` 保存独立的 120 题 draft 版本：原 100 道可回答题保持不变，新增 20 道不可回答题，继续复用同一份 1000 篇 corpus。新增题按上游 1doc/2docs/3docs 分为 7/7/6，split 为 12/4/4；最终类型为 13 道 `out_of_scope` 和 7 道 `insufficient_detail`。

该版本已经完成静态 gold/近重复排除和隔离索引 Dense/BM25/Hybrid 无阈值 Top50 审计。第一轮发现并淘汰了一道可以跨两篇现有文档拼出完整答案的候选，淘汰证据和防重选决策保存在 `v3/rejected_review/`、`v3/selection_overrides.json`。最终集合的检索报告在 `v3/retrieval_top50.json`，摘要在 `v3/retrieval_summary.md`。

检索审计使用 `v3/runs/unanswerable-audit-v1/chroma/` 下的隔离索引副本，Embedding 查询在 `cuda:0` 运行，BM25 在 CPU 运行，不调用 Reranker 和 LLM，也不使用生产数据库。20 题目前仍为 `draft`、`whole_corpus_checked=false`；自动和 AI 辅助复核不能替代正式人类 reviewer 的 strict 审核。

## 固定来源与实际 schema

上游仓库固定为 [IAAR-Shanghai/CRUD_RAG](https://github.com/IAAR-Shanghai/CRUD_RAG)，commit 为 `1aace383994e1f68efa12cf2a8e2dadfb4102ceb`。所有来源 URL 都包含这个 commit；下载文件和 SHA-256 记录在 `selection_manifest.json` 与 `provenance.json`。

`source/split_merged.json` 的实际顶层字段和数量为：

| 字段 | 条数 |
|---|---:|
| `event_summary` | 2000 |
| `continuing_writing` | 1999 |
| `hallu_modified` | 1268 |
| `questanswer_1doc` | 800 |
| `questanswer_2docs` | 797 |
| `questanswer_3docs` | 797 |

QA 条目的实际字段是：

- `questanswer_1doc`: `ID`, `event`, `news1`, `questions`, `answers`
- `questanswer_2docs`: 上述字段加 `news2`, `thoughts`
- `questanswer_3docs`: 上述字段加 `news2`, `news3`, `thoughts`

原始 split 文件大小为 26,866,460 字节，SHA-256 为
`b199fb281ac526c1c8da7364cad7ea22b7f6fe1d4167e511edbdda8ad3bd943a`。

## 抽样和语料构成

使用 seed `20260819`，按 `SHA-256(seed|类别|上游 ID|规范化问题)` 固定排序，从三类中抽取 `34/33/33`，没有发生再分配。过滤了缺 ID、问题、答案、gold 文本、同题 gold 文档重复和重复问题；过滤后有效候选数分别为 `800/789/783`。

- 100 道问题：`questanswer_1doc=34`、`questanswer_2docs=33`、`questanswer_3docs=33`
- 193 篇去重后的 gold 文档：`corpus/support/<sha-prefix>.txt`
- 807 篇 non-gold distractor：`corpus/distractor/distractor-0001.txt` 至 `distractor-0807.txt`
- 总数：1000 篇 `.txt`

gold 文档由选中题目的 `news*` 字段规范化后按 SHA-256 去重。distractor 使用固定顺序的上游分片 `documents_dup_part_1_part_1`，实际扫描 811 条、接受 807 条、因 SimHash 汉明距离不大于 5 拒绝 4 条。这里的 distractor 只表示 non-gold/去重后的候选，不声称已经人工证明与问题语义无关。

每个 gold 文档有一条精确、唯一可定位的 evidence。构建器用问题+答案的字符二元组重合度选择单句或最多三句窗口；本批次 199 条 evidence 均未使用完整文档 fallback。`keypoints` 使用完整 `answers`，以确保覆盖规则成立。当前 `easy / medium / hard` 只是按 1 / 2 / 3 篇 gold 文档映射的工程分层，不是人工标注的难度结论。

所有题目都是 `answerable=true` 的上游 QA 题。`review.status=draft`，`reviewer/reviewed_at=null`，三个 checked 字段均为 `false`；这不是人工验收结果，正式 gold 前仍需逐题审核。

## 文件树

```text
eval/crud_rag_1000/
├── source/
│   ├── split_merged.json
│   ├── tree.json
│   ├── UPSTREAM_README.md
│   └── documents_dup_part_1_part_1
├── corpus/
│   ├── support/       # 193 个 gold txt
│   └── distractor/    # 807 个 non-gold txt
├── questions_v3.json
├── corpus_v3_manifest.json
├── selection_manifest.json
├── provenance.json
├── prepare_dataset.py
├── run_retrieval_sweep.py
├── test_dataset.py
└── README.md
```

`source/` 中固定下载文件的大小和 SHA-256 也以机器可读形式保存在 provenance 文件中；当前分片大小为 3,984,745 字节，SHA-256 为 `bea3ca0f1c140442655eaa25695f169499ac2fd115b5e5291d51c2c1467c9dd5`。

## 重建和验证

如果从空目录重建，先在本目录下下载固定来源（不需要下载完整仓库）：

```powershell
$commit = '1aace383994e1f68efa12cf2a8e2dadfb4102ceb'
curl.exe -L --fail --output source/split_merged.json `
  "https://raw.githubusercontent.com/IAAR-Shanghai/CRUD_RAG/$commit/data/crud_split/split_merged.json"
curl.exe -L --fail --output source/tree.json `
  "https://api.github.com/repos/IAAR-Shanghai/CRUD_RAG/git/trees/$commit`?recursive=1"
curl.exe -L --fail --output source/UPSTREAM_README.md `
  "https://raw.githubusercontent.com/IAAR-Shanghai/CRUD_RAG/$commit/README.md"
curl.exe -L --fail --output source/documents_dup_part_1_part_1 `
  "https://raw.githubusercontent.com/IAAR-Shanghai/CRUD_RAG/$commit/data/80000_docs/documents_dup_part_1_part_1"
```

在仓库根目录 `D:\ProgramFiles\RAG` 执行：

```powershell
.\.venv\Scripts\python.exe eval/crud_rag_1000/prepare_dataset.py --build
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  --basetemp eval/crud_rag_1000/.pytest-tmp `
  eval/crud_rag_1000/test_dataset.py
```

上述离线测试当前为 `11 passed`。它会核对 100/1000 计数、34/33/33、唯一 ID/问题、选中问题/答案/gold 正文与固定上游逐项一致、evidence 文档存在且 snippet 唯一精确可定位、题目到 gold 文档的闭包、gold/distractor 正文不交叉、每篇 distractor 的来源行号与 hash、manifest 与当前 corpus 一致、Dataset v3 non-strict validate、项目评测适配层能递归发现全部 1000 篇文档、source provenance/hash，以及性能 runner 的路径隔离。

也可以直接运行项目现有的 Dataset v3 non-strict 校验：

```powershell
.\.venv\Scripts\python.exe -m eval.dataset_v3 validate `
  --dataset eval/crud_rag_1000/questions_v3.json `
  --corpus eval/crud_rag_1000/corpus `
  --manifest eval/crud_rag_1000/corpus_v3_manifest.json
```

明确的实际评测入口如下；它是离线 retrieval sweep，不调用 DeepSeek，但需要本机已有 embedding/reranker 缓存。包装脚本把索引和报告固定写到本目录的 `runs/`，因此不会把本数据集的运行产物散落到项目其他目录。本次构建只做了针对性数据测试，没有运行完整 100 题性能评测：

```powershell
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
.\.venv\Scripts\python.exe eval/crud_rag_1000/run_retrieval_sweep.py `
  --label crud-rag-1000-preflight `
  --candidate-ks 15 20 30
```

## 许可和范围限制

上游 README 有 Apache-2.0 徽章，但仓库根目录缺少 `LICENSE` 文件，GitHub license 元数据也为空，因此许可信息不完整。本目录只用于本地评测，不作上游许可的补充解释或再分发声明。

本目录没有修改现有 `eval/dataset/questions_v3.json`，没有提交 git，也没有安装依赖。distractor 不是人工证明的语义无关文档，当前 review 也仍是 draft；在把它作为正式 gold/发布基准前，需要人工审核问题、答案、证据和 corpus 隔离。
