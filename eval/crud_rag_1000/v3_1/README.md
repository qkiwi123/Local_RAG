# CRUD_RAG v3.1：120题独立修正版

> 2026-08-27；只修正题库，不覆盖旧版本。
>
> 数据集 ID：`crud-rag-1000-120q-v3.1`；数据格式仍为 Dataset v3（`schema_version=3`）。
>
> 当前状态（2026-08-31）：固定GPU基线及V3-5 A—E/B2已完成；E只选出development离线门控策略，120题仍全部为 `draft`，没有调用生成模型API或接入生产门控。

最新结果见[固定基线验收报告](新增测试集和运行优化v3方案-v3.1基线验收-20260828.md)：1000篇/2914块，文档Hit@5 99%，全部gold召回94%，暖态均值0.2849秒；20道不可回答题均非空，门控尚待优化。
下文的修题/原版保护记录对应2026-08-27阶段；生成器的provenance和风险报告保留当时状态，不回填新阶段事实。

## 快速入口

- [新版题库](questions_v3_120q.json)：项目评测读取的120题完整JSON。
- [10题修正对照与证据](10题修正对照与证据.md)：优先阅读；逐题列出前后问题、答案、直接证据与保留限制。
- [修正后风险队列](AI预审风险队列-v3.1.md)：10题修正状态及其余未处理风险。
- [实施与验收说明](新增测试集和运行优化v3方案-v3.1修订说明.md)：怎么做、为什么、结果、原版保护与后续评测边界。
- [完整变更记录](change_manifest.json) / [新旧ID映射](id_mapping.json)：保留10题前后完整对象，以及全部120题的对应关系。
- [校验结果](validation_report.json) / [来源与产物哈希](provenance.json) / [原版保护快照](source_preservation.json)。

## 当前规模

| 项目 | v3父版 | v3.1新版 |
|---|---:|---:|
| 总题数 | 120 | 120 |
| 可回答 / 不可回答 | 100 / 20 | 100 / 20 |
| 可回答1/2/3文档题 | 34 / 33 / 33 | 37 / 33 / 30 |
| development / validation / frozen_test | 72 / 24 / 24 | 72 / 24 / 24 |
| evidence条目数 | 199 | 193 |
| 当前参与gold的不同文档 | 193 | 188 |
| corpus总文档数 | 1000 | 1000 |

本轮仅10题对象发生变化，其中7题查询文本改变、5题因必要文档数改变而更换ID。
其余110题（包括全部20道不可回答题）与父版完全一致。物理文件仍是193篇support、
807篇distractor；不再参与gold的5篇support文件仍留在原corpus内，没有删除、移动或重写。

## 重要纠错

H-A9的原始全文明确写“阿尔及利亚朋友们”；先前答复建议改成阿联酋，是误用了错配的
`source.upstream_event`，现已撤回。新版保留阿尔及利亚，并删除“智利强调军事交流”的不当归纳。
H-A7的全文也确实有“特别关注日本”，新版将它与“美国及其地区盟友”一并纳入答案和证据。
这两项以本版全文核对记录为准，不再沿用先前答复中的错误说明。

## 隔离与保留

- 原 `../v3/`、原100题、原corpus清单和原设计文档均未修改；61个原始静态文件已做哈希对照。
- 复用 `../corpus/` 和 `../corpus_v3_manifest.json`，1000篇文档逐一匹配清单。
- 修题阶段未访问数据库或运行模型。后续基线仅只读核对生产设置及保护哈希，在新的测试库运行Embedding/Reranker；未写生产库、未调用项目LLM API。
- `source.upstream_task/upstream_id/upstream_event`保留上游原始内容，不能当作纠正后的事实；
  已知错配写在逐题修改记录中。仅可回答题的`source.gold_document_count`反映新版必要gold数量。
- 不自动把本版替换到项目默认评测路径；今后使用本版需显式选择本目录入口。

## 校验与复现

本轮验收：26项测试通过；项目非严格数据校验0错误、120条draft提醒。
严格校验按预期未通过（620条人工审核条件未满足），没有伪造`verified`。
首次测试因临时目录父路径不存在造成1项准备错误，复验已通过；两次记录均保存在`.validation/`。

以下命令在仓库根目录执行，均不做检索推理：

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe eval\crud_rag_1000\v3_1\build_revision.py --check

.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider `
  --basetemp eval\crud_rag_1000\v3_1\.validation\pytest-manual-01 `
  eval\crud_rag_1000\v3_1\test_revision.py -q

.\.venv\Scripts\python.exe eval\crud_rag_1000\v3_1\run_retrieval_baseline.py `
  --profile gpu --label preview-v31 --dry-run
```

复验时使用新的临时子目录名，保留现有测试记录。生成规则为`correction_spec.json`；
`build_revision.py`默认只允许在本目录生成不存在的产物，遇到内容不同的现有文件直接拒绝覆盖。
`--check`只读比对，能验证整版生成结果的可复现性。

## 2026-08-28：固定GPU检索基线已完成

`run_retrieval_baseline.py`适配新版题库与`v3_1/runs/<label>/`输出目录，提供不加载模型的
`--dry-run`参数预览。经用户确认，本次用新增`execute_frozen_baseline.py`包装器固定隐式默认值、
记录保护哈希/GPU采样并执行了一个k15配置。不是候选扫描，也不说明15已经最优。

需要区分两件事：语料没有改变，因此修题本身不要求重新生成文档向量；但项目目前的基线命令
按设计从空库入库，真正运行此入口会创建**新的隔离测试索引**，不是直接复用旧索引。
2026-08-28基线按此规则完成新建库；没有改成复用旧索引，也没有覆盖旧库。

本次运行结果在`runs/gpu-baseline-120q-v31-k15-20260828/`，验收与对照在
`baseline_audits/gpu-baseline-120q-v31-k15-20260828/`。报告分别对照未改110题与修正10题，
没有把旧CPU/GPU结果更名充当新版结果。实际模型设备均验证为cuda:0，相关前置测试71项通过。

去掉的重复报道仍在语料中，严格文档ID指标仍可能把命中替代报道判成未命中；该限制在风险
报告中保留，不能把调整gold后的分数变化都解读成检索能力提升。
截至2026-08-28固定基线阶段结束时，V3-5参数校准和门控优化尚未执行。

## 2026-08-31：V3-5 A—F及补充B2状态

隔离调参产物位于`tuning/v35-a-baseline-20260829/`。A完成索引副本与参数接缝，B/C/D完成模式、Dense候选、Reranker和上下文预算，补充B2验证Hybrid BM25/RRF没有超过Dense。E随后只读D固定Trace评测16个门控策略，选择M2：development中0/60可回答误拒、11/12不可回答拦截，并通过Top1 ±0.02和Dense distance ±0.005扰动检查。

E没有重新检索、加载模型或调用LLM，也没有修改业务retriever、schema和生产默认值。F随后冻结M2和两个上下文候选，在validation 24题完成72次正式GPU检索：M2为1/20误拒、1/4拦截，Top10/Top5均为18/20 all-gold且相对原始Hybrid基线各回归2题。两个候选均不合格，F结论为`not_adopted`；G、S7业务门控和V3-6均未执行。完整证据和当前边界见[new_docs F验收](../../../new_docs/测试/TEST-006-数据集与评测验收.md#v35-f)。
