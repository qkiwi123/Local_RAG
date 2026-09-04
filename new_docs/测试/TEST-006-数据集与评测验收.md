# TEST-006 数据集与评测验收

## 文档信息

- 文档ID：TEST-006
- 文档类型：测试
- 状态：测试中
- 创建日期：2026-08-27
- 最后更新：2026-08-31
- 证据口径：A—F及补充B2已完成；F执行验收和164项回归通过，但M2/候选效果门槛失败并判不采用；G、生成模型和业务接入未执行，题库仍draft。
- 导航：[本类目录](./README.md) · [文档中心](../README.md)

## 1. 目标与对象

关系：验证[DEV-011 评测CLI与数据集](../开发/DEV-011-评测CLI与数据集.md)和[数据集契约](../设计/DES-007-评测隔离与数据契约.md#datasets)。区分数据结构通过、人工审核通过和真实评测完成。

<a id="cases"></a>
## 2. 用例与方法

下表“本次实际”保留2026-08-27整理阶段记录；后续固定基线验收单列于第3节，不把未跑过的整份测试文件改成已通过。

| 用例 | 数据/输入 | 预期 | 测试入口 | 本次实际 |
|---|---|---|---|---|
| E01 | parser/manifest/offset/hash | 非严格校验发现不一致 | [test_dataset_v3.py](../../tests/test_dataset_v3.py) | 未执行 |
| E02 | draft review | strict拒绝正式验收 | [test_dataset_v3.py](../../tests/test_dataset_v3.py) | 未执行 |
| E03 | CLI隔离目录/错误参数 | 不写生产库、错误前置退出 | [test_eval_cli.py](../../tests/test_eval_cli.py) | 未执行 |
| E04 | 文档/证据/引用各指标 | 分开计算，不写死v1 | [test_eval_metrics.py](../../tests/test_eval_metrics.py)、[v3指标](../../tests/test_eval_v3_retrieval_metrics.py) | 未执行 |
| E05 | v3.1确定性修订 | 10题变化、110题保留、原语料不改 | [test_revision.py](../../eval/crud_rag_1000/v3_1/test_revision.py) | 未执行 |

<a id="evidence"></a>
## 3. 当前数据事实与历史结果

本次直接读取三个JSON，确认：项目general-courseware-v1有41个draft审核组；CRUD_RAG v3与v3.1各120个draft；三者parser均structured-v2、schema_version均3。这是文件统计，不是校验器/检索运行。

| 资料 | 当前证据 | 门禁 |
|---|---|---|
| 项目Dataset v3 | [题库](../../eval/dataset/questions_v3.json)：35单题、6组对话 | 人工签核未完成 |
| 原人工审核包 | [review.md](../../eval/dataset/questions_v3_review.md)仍写structured-v1、hash 7901b552… | 与当前题库b8afd21f…不绑定，重新导出后才能用于当前签核 |
| CRUD_RAG v3 | [120题](../../eval/crud_rag_1000/v3/questions_v3_120q.json)：父版draft | CPU/GPU只有preflight |
| CRUD_RAG v3.1修题阶段 | [validation_report.json](../../eval/crud_rag_1000/v3_1/validation_report.json)：120题/1000文档、10题修订、5个ID变更、7个查询变化 | 当时retrieval_rerun=false；后续基线单独记录，不回填旧provenance |
| v3.1历史离线验收 | [验收记录](../../eval/crud_rag_1000/v3_1/.validation/README.md)：首次25通过/1准备错误，复验26项通过 | strict仍未通过，不伪造verified |

v3.1实际保留100/20可回答/不可回答；新版1/2/3文档题37/33/30，证据193条、活跃gold文档188篇。以上规模来自本次读取的已有报告，不是新生成效果分数。H-A9/H-A7纠正应以[原文对照](../../eval/crud_rag_1000/v3_1/10题修正对照与证据.md)为准。

<a id="baseline-v31"></a>
### 3.1 2026-08-28固定GPU基线已完成

读取[验收报告](../../eval/crud_rag_1000/v3_1/新增测试集和运行优化v3方案-v3.1基线验收-20260828.md)、[analysis.json](../../eval/crud_rag_1000/v3_1/baseline_audits/gpu-baseline-120q-v31-k15-20260828/analysis.json)及[最终JUnit](../../eval/crud_rag_1000/v3_1/.validation/baseline-acceptance-20260828/acceptance-tests.xml)，确认上一阶段完成：

- 题库hash为`f25b257fc5e737dee4af4f701bca375493614211ec4dc0b0af77190650098808`，1000篇/2914块，120/120题结果及Trace。
- 新Chroma/metadata位于v3_1独立run，生产数据和输入/代码保护哈希未变，生成模型未调用，两模型实际设备cuda:0。
- 前后同组71项测试均通过、0跳过；包含test_revision、入口保护、v3指标、检索runtime/Trace及评测接缝，不宣称所有eval测试都已执行。
- 原评分纯函数对保存JSONL重算与原生汇总完全相等；37项产物哈希封存、61项原静态资料保护、1000篇manifest匹配。
- 10道修正题均召回新版必要gold；未改110题质量指标与旧版一致。完整质量/速度数据见[TEST-002](./TEST-002-检索质量与性能.md#baseline-v31)。

这些是已存在的真实基线证据，本次文档更新不再次运行检索。120题仍draft，严格人工审核未通过，实际答案/引用/LLM拒答质量未测。

## 4. 安全复核入口

```powershell
.\.venv\Scripts\python.exe eval\crud_rag_1000\v3_1\build_revision.py --check
.\.venv\Scripts\python.exe -m eval.dataset_v3 validate --dataset eval/dataset/questions_v3.json --corpus eval/corpus --manifest eval/dataset/corpus_v3_manifest.json
```

本次方案文档更新未执行上述功能校验；build_revision --check已在前一固定基线阶段通过。strict须真实审核完成才允许成功；新版审核包应输出新路径，避免覆盖历史证据。真实生成仍须另行确认。

## 5. Bug、性能与最终结论

[BUG-002 结构化引用指标版本误判](../测试/BUG-002-结构化引用指标版本误判.md)修复了结构化引用计分，回归为test_structured_location_is_valid_without_page_number与test_structured_v2_section_without_page_number。当前已有v3.1检索基线、B/C/D/B2检索调参、E离线门控及F validation否决结果，没有LLM拒答、G性能复测或S7业务结果；旧CPU/GPU与新版GPU在[TEST-002](./TEST-002-检索质量与性能.md)分开记录。[OPT-003](../优化/OPT-003-候选深度与拒答门控.md)不能跳过审核和分区约束。

<a id="tuning-gates"></a>
## 6. V3-5分区、风险与阶段验收

| 分区 | 当前题数 | 拟定用途 | 当前基准：可回答all-gold / 不可回答非空 |
|---|---:|---|---|
| development | 72（60+12） | 参数搜索、误差分析、门控校准 | 56/60；12/12 |
| validation | 24（20+4） | 只比较预先入选的最多2候选和基准，不继续调阈值 | 19/20；4/4 |
| frozen_test | 24（20+4） | 后续V3-6回归，本轮不参与选型 | 19/20；4/4 |

三者已被查看，保留原split以便对齐，但不再称作未污染盲测。内容未人工verified不阻止用户已接受范围内的draft探索，不能把探索结果升级为正式审核结论。

已完成A—F及补充B2：索引副本/hash、参数实际生效、split过滤、缓存/重放一致性、无API/GPU门禁、模式与候选参数扫描、上下文预算、Hybrid词法参数、离线多信号门控及固定M2的validation验证。F没有候选通过；G同索引交替3轮计时不执行，S7业务门控仍需新方案和另行授权。
每配置保留同预算@5与实际@K、分阶段gold保留、门控混淆矩阵、context预算和改善/回归题目；门槛建议以[OPT-003验收提议](../优化/OPT-003-候选深度与拒答门控.md#acceptance)为准，不把目标数值填作结果。

继续保留10修正/110未改以及4高风险不可回答、25中风险、21检索指标压力、60旧预审未见明显内容问题的分层。
旧风险说明有两处一字符ID笔误，本次风险统计使用[analysis.json中的显式映射](../../eval/crud_rag_1000/v3_1/baseline_audits/gpu-baseline-120q-v31-k15-20260828/analysis.json)，不改题库和原报告。

<a id="v35-a"></a>
## 7. 2026-08-29 V3-5 A验收

证据：[实验协议](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/protocol.json)、[A验收](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-a-acceptance.json)、[最终核验与哈希](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-a-final-verification.json)、[JUnit](../../eval/crud_rag_1000/v3_1/.validation/v35-a-final2-tests-20260829.xml)。

| 检查 | 实际结果 | 结论 |
|---|---|---|
| 存储隔离 | 封存测试库复制到新实验；1000 documents、1000 units、2914 embeddings、quick_check=ok | 通过；生产39文件仍匹配2026-08-28快照 |
| 分区与参数 | 只执行development 72（60+12）；effective_config 72/72相符 | 通过；validation/frozen_test未运行 |
| 可回答等价 | 最终Top5顺序60/60一致；Hit 98.33%、Recall 95.83%、MRR 0.9306、all-gold 56/60、coverage 95.67% | 与历史development基线完全一致 |
| Dense/最终差异 | 历史run与A有10题Dense集合变化；只有2道不可回答题传播到最终Top5，BM25为72/72一致 | 显式限制；跨进程单次ANN不能直接作为参数收益 |
| GPU/API | Embedding、Reranker均actual cuda:0；LLM模块未导入；0出站连接 | 通过 |
| 缓存 | 72题真实运行1次；72/72同配置缓存重放1次，4.104s | 一致；不当作检索性能 |
| 回归 | 针对入口/Trace/runtime/评分/修题及Chroma运行簿记共83项通过 | 通过；首次81通过/1失败是basetemp位置不合约，纠正路径并补回归后复验 |

真实运行wall 73.801s；首题冷启动52.857s，后71题mean 0.2575s，P50 0.258s、P95 0.284s。A只证明入口和同索引对照可用，不从一次顺序运行推断稳定性能提升。

初版summary按“所有72题最终候选必须完全一致”判失败，原文件保留；检查证明差异仅在2道不可回答题且起于Dense ANN。修订验收口径要求可回答60/60、质量及配置全部一致，并用新的`phase-a-acceptance.json`记录通过，没有覆盖初始失败。

Chroma首次打开副本会增加`acquire_write`运行簿记，导致chroma.sqlite3字节哈希变化；HNSW、metadata.db、embedding计数和quick_check均不变。因此运行期门禁使用不可变文件hash加逻辑数据库契约，prepare复制仍要求逐字节相等。

<a id="dense-variance"></a>
## 8. Dense ANN差异补充验收

证据：[诊断报告](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/diagnostics/Dense差异来源与波动范围-20260829.md)、[机器汇总](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/diagnostics/dense-variance-analysis.json)和[诊断探针](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/diagnostics/dense_variance_probe.py)。

| 检查 | 实际结果 | 验收解释 |
|---|---|---|
| Embedding稳定性 | 15个独立GPU进程中，10道受影响题的每题Embedding哈希均只有1种 | 排除GPU查询Embedding随机变化 |
| Chroma状态 | 2914 embeddings；metadata seq=2914，HNSW vector seq=2000，queue 915行/914条尾段 | 确认持久化HNSW未覆盖全部向量 |
| 生命周期对照 | 全新隔离索引在入库同进程与重开后，指定题Top15共同12个、每侧替换3个 | 直接复现进程生命周期导致的候选变化 |
| 跨进程波动 | 10题中6题出现2—3种Top15；共同12—15、并集15—17、每侧最多替换2、最低Jaccard 0.765 | 当前样本的经验范围，不是理论上界 |
| 精确oracle | ANN与2914向量精确余弦Top15重合11—15，实测Recall@15为73.33%—100% | ANN候选不是精确TopK，不可把微小变化视为向量变化 |
| 完整链路 | 150次中变化题数为Dense 5、BM25 0、RRF 3、Reranker 3、最终Top5 2；同进程重复稳定 | 来源在Dense之前，不是Reranker首先制造差异 |
| 质量影响 | 1道可回答题只在第5位交换非Gold，两篇Gold及证据持续保留；另1道为不可回答题 | 本次未观察到Hit/Recall/all-gold/evidence变化，不等于今后必然无影响 |

A的配置生效、GPU、隔离和可回答质量等价结论仍成立；但“直接以复制库control跨进程单跑比较细微差异”的执行假设不成立。B随后采用同进程首尾control并通过，见第9节。

<a id="v35-b"></a>
## 9. 2026-08-29 V3-5 B验收

证据：[协议](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-b/v35-b-mode-ablation-20260829/protocol-completed.json)、[机器汇总](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-b/v35-b-mode-ablation-20260829/phase-b-summary.json)、[解释结论](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-b/v35-b-mode-ablation-20260829/phase-b-conclusion.md)、[最终核验](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-b/v35-b-mode-ablation-20260829/phase-b-final-verification.json)及[JUnit](../../eval/crud_rag_1000/v3_1/.validation/v35-b-final-tests-20260829.xml)。

| 检查 | 实际结果 | 结论 |
|---|---|---|
| 范围/配置 | development 72题；Dense/BM25/Hybrid×重排开关六组；仅mode与reranker_enabled变化 | 通过；C参数未混入 |
| 真实运行 | 七轮504道次计分+1次预热；wall 110.237s | 通过；没有使用跨配置结果缓存 |
| ANN控制 | control首尾Dense raw/eligible、BM25、RRF、Reranker、selected均72/72顺序相同，质量完全相同 | 通过；配置差异可归因 |
| GPU/API | Embedding与Reranker均actual cuda:0；LLM模块未导入；0出站连接 | 通过 |
| 数据隔离 | quick_check=ok；1000 documents/units、2914 embeddings；封存37文件与生产39文件无变化 | 通过 |
| 回归 | 86通过、0失败、0错误、2个依赖弃用警告 | 通过 |

质量结果：Hybrid+Reranker与Dense+Reranker全部聚合指标及60题逐题优先质量均等价；BM25+Reranker回退。Hybrid不重排的Recall、MRR和Evidence汇总更高、描述性耗时更低，但有7题改善和2题回归；Dense不重排all-gold仅52/60；六组不可回答均12/12非空。B通过只说明模式贡献已测，不表示候选已选为生产默认或拒答问题已解决。

<a id="v35-c"></a>
## 10. 2026-08-29 V3-5 C验收

证据：[协议](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-c/v35-c-dense-quality-20260829/protocol-completed.json)、[K选择](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-c/v35-c-dense-quality-20260829/k-selection.json)、[机器汇总](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-c/v35-c-dense-quality-20260829/phase-c-summary.json)、[解释结论](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-c/v35-c-dense-quality-20260829/phase-c-conclusion.md)、[最终核验](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-c/v35-c-dense-quality-20260829/phase-c-final-verification.json)及[最终JUnit](../../eval/crud_rag_1000/v3_1/.validation/v35-c-final2-tests-20260829.xml)。

| 检查 | 实际结果 | 结论 |
|---|---|---|
| 范围/配置 | development 72题；4个Dense K及6个distance，去重后9个唯一Dense配置；固定Reranker K15、Top5、每文档3块 | 通过；validation/frozen_test未运行，D参数未混入 |
| 真实运行 | 12轮864道次计分+1次预热；wall 234.99s | 通过；没有跨配置结果缓存 |
| ANN控制 | Dense与Hybrid control首尾逐阶段72/72精确一致；包括rank、distance、eligible/filter及Reranker score | 通过；同进程参数差异可归因 |
| Trace与参数 | 真实`dense/reranker/selected`阶段、原始K、阈值资格、重排输入和最终结果逐行核对；Dense的fusion池标为不适用 | 通过；未用空伪阶段代替真实Dense Trace |
| GPU/API | Embedding与Reranker均actual cuda:0；LLM模块未导入；0出站连接 | 通过 |
| 数据隔离 | quick_check=ok；1000 documents/units、2914 embeddings；封存37文件与生产39文件无变化 | 通过 |
| 回归 | 受控basetemp下93通过、0失败、4个依赖/缓存清理警告 | 通过；首次92通过/1失败仅因默认临时目录不满足v3_1准备约束，原JUnit保留 |

质量结果：同进程Dense K15/0.67为Hit 98.33%、Recall 96.67%、MRR 0.9306、all-gold 57/60、多文档完整37/40、Evidence 96.51%。K10回归2题；K30/50与K15最终质量及Top5完全一致。阈值0.55/0.60分别回归2/1题；0.65/0.70/0.75无硬质量提升。故C仍选K15/0.67，不改生产默认值。B/C相同配置跨进程相差1道完整gold属于已确认ANN生命周期波动，不计为C收益。C通过只授权停在C结论，不自动进入D。

<a id="v35-d"></a>
## 11. 2026-08-30 V3-5 D验收

证据：[协议](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-d/v35-d-rerank-context-quality-20260830/protocol-completed.json)、[机器汇总](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-d/v35-d-rerank-context-quality-20260830/phase-d-summary.json)、[解释结论](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-d/v35-d-rerank-context-quality-20260830/phase-d-conclusion.md)、[最终核验](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-d/v35-d-rerank-context-quality-20260830/phase-d-final-verification.json)及[JUnit](../../eval/crud_rag_1000/v3_1/.validation/v35-d-final-tests-20260830.xml)。

| 检查 | 实际结果 | 结论 |
|---|---|---|
| 范围/配置 | development 72题；9个唯一真实Dense/Reranker配置；15个合法TopK/单文档配额重放配置 | 通过；validation/frozen_test未运行，E门控未混入 |
| 真实运行 | 12轮864道次计分+1次预热；wall 346.08s | 通过；没有跨配置结果缓存 |
| ANN控制 | Dense与Hybrid control首尾从raw/eligible、distance、重排分数到selected均72/72精确一致 | 通过；D同进程参数差异可归因 |
| Trace/上下文重放 | 从隔离Chroma补齐Trace缺失的document/unit/offset元数据；15组重放按现有单文档配额语义执行，真实配置逐题核对一致 | 通过；不是仅凭不完整Trace臆造上下文 |
| GPU/API | Embedding与Reranker均actual cuda:0；峰值allocated/reserved约2401/2482MiB；LLM模块未导入；0出站连接 | 通过 |
| 数据隔离 | quick_check=ok；1000 documents/units、2914 embeddings；封存37文件与生产39文件无变化 | 通过 |
| 质量与成本 | D20/R20是最小真实质量平台；Top10/配额2质量最高但上下文约翻倍，Top5/配额2为成本候选 | 通过；两候选均不作为生产默认 |
| 回归 | 101通过、0失败、0错误、2个依赖弃用警告 | 通过 |

D同进程control为Hit 98.33%、Recall 95.83%、MRR 0.9306、all-gold 56/60、Evidence 95.67%。Dense20/Reranker20在Top5/配额3下改善到Recall 96.67%、all-gold 57/60、Evidence 96.51%，逐题改善1道且无回归；更大的真实窗口没有新增质量。

质量优先候选是D20/R20的Top10/配额2：Recall 98.89%、all-gold 59/60、多文档完整39/40、Evidence 98.89%，改善4道且无回归，但平均stored tokens从1495增至2888、chunk从5增至9.83。成本平衡候选是Top5/配额2：保持57/60并把平均stored tokens降至1480。两者的不可回答题仍12/12非空，E门控问题未解决。

C/D相同配置分属不同进程，all-gold 57/60与56/60的差异按已确认ANN生命周期波动处理，不计为D回退。D未修改业务算法、索引或生产默认值，也未使用validation/frozen_test；完成只授权停在D结论，不自动进入E。

<a id="v35-b2"></a>
## 12. 2026-08-31 V3-5补充B2验收

证据：[机器汇总](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-b2/v35-b2-hybrid-bm25-fp16guard-20260831/phase-b2-summary.json)、[解释结论](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-b2/v35-b2-hybrid-bm25-fp16guard-20260831/phase-b2-conclusion.md)、[最终核验](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-b2/v35-b2-hybrid-bm25-fp16guard-20260831/phase-b2-final-verification.json)及[JUnit](../../eval/crud_rag_1000/v3_1/.validation/v35-b2-final6-tests-20260831.xml)。

| 检查 | 实际结果 | 结论 |
|---|---|---|
| 范围/配置 | development 72题；14个唯一Hybrid配置；BM25 K/k1/b与RRF受控坐标扫描 | 通过；B2验收时validation/frozen_test、E及D复查未运行 |
| 真实运行 | 17轮1224次正式检索+1次预热；wall 462.591s；8次BM25索引预热排除在质量/时延外 | 通过；没有跨配置结果缓存 |
| 因果控制 | 每配置Dense 72/72等于同期control；BM25 K前缀、RRF上游、RRF重算均稳定；双control首尾逐阶段72/72精确一致 | 通过；坐标差异可归因 |
| FP16/Top5 | 18388次共享分数比较，205对/59题变化，max 0.01171875≤0.02；604组相同候选集Top5全相同；3次反转均为第7/8名完全同分 | 通过；容差不允许业务Top5漂移 |
| GPU/API | Embedding与Reranker均actual cuda:0；峰值约2397/2482MiB；LLM未导入；0出站连接 | 通过 |
| 数据隔离 | quick_check=ok；1000 documents/units、2914 embeddings；封存37文件与生产39文件无变化 | 通过 |
| 质量 | 最佳Hybrid K50/k1=1.5/b=0.75/RRF60与Dense全部硬指标平局，逐题改善/回归/混合损失0/0/0 | 通过；保留Dense，不触发D复查 |
| 回归 | 134通过、0失败、0错误、2个依赖弃用警告 | 通过 |

同期Dense与最佳Hybrid均为Hit 98.33%、Recall 96.67%、MRR 0.9306、all-gold 57/60、多文档完整37/40、Evidence 96.51%；Hybrid最终集合虽有26/72题不同，未产生已测质量增益。BM25 K10—30及k1=2.0配置有可回答质量回归；其他通过门禁的9个配置均只平局。12道不可回答仍全部非空。

首次运行在补全跨配置因果不变量后主动中止；第二次完整运行被临时1e-6共享分数容差拒绝。两次失败产物均保留并标记`quality_result_valid=false`，没有覆盖或冒充有效结果。最终运行使用新run id及0.02 FP16容差+Top5硬门禁，未修改业务算法、索引、生产默认值，也未自动进入E。

<a id="v35-e"></a>
## 13. 2026-08-31 V3-5 E验收

证据：[策略清单](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-e/v35-e-evidence-gate-guard-20260831/policies.json)、[逐题结果](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-e/v35-e-evidence-gate-guard-20260831/per-question.jsonl)、[机器汇总](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-e/v35-e-evidence-gate-guard-20260831/phase-e-summary.json)、[解释结论](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-e/v35-e-evidence-gate-guard-20260831/phase-e-conclusion.md)、[最终核验](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-e/v35-e-evidence-gate-guard-20260831/phase-e-final-verification.json)及[JUnit](../../eval/crud_rag_1000/v3_1/.validation/v35-e-final3-tests-20260831.xml)。

| 检查 | 实际结果 | 结论 |
|---|---|---|
| 范围/输入 | development 72题；固定D20/R20 Trace及Top10/配额2、Top5/配额2 | 通过；validation/frozen_test未读取 |
| 策略 | 1关闭+7单阈值+8多信号，共16项 | 通过；预注册矩阵没有结果后扩网格 |
| 严格门槛 | 0/60误拒、至少9/12拦截；Top1 ±0.02、两项distance ±0.005的8角扰动全部稳定 | 通过；不是加权选择 |
| 选择 | M2：low -2.5、high 0.5、Dense最佳0.52、Top1 Dense 0.55 | 0/60误拒、11/12拦截、0翻转 |
| M8诊断 | 0/60误拒、12/12表面拦截，但2题扰动翻转 | 严格淘汰，不因表面最好采用 |
| 上下文绑定 | Top10和Top5均从12/12不可回答非空降至1/12；可回答回归/混合0/0 | 通过；门控信号来自共同上游 |
| 执行边界 | 0次真实检索、0次LLM；wall 6.86s是离线文件校验/重放，不是线上时延 | 通过；不宣称GPU或性能收益 |
| 隔离 | quick_check=ok；1000 documents/units、2914 embeddings；封存37、生产39文件不变 | 通过 |
| 回归 | 145通过、0失败、0错误、2个依赖弃用警告 | 通过 |

首次run的门控计算通过，但最终验收读取存储统计时误用字段层级；该目录追加`verification-failed.json`并标记`quality_result_valid=false`。修正字段映射、增加契约测试和生产/封存检查后使用新run id完整重跑，再执行145项回归和最终验收；没有覆盖失败证据。

E只固定development离线策略M2。`unanswerable_llm_entry_rate=0.0833`表示门控后仍有1/12题保留非空上下文，是流程代理而非真实LLM调用。F随后已执行但否决M2；G、S7、生产默认值和BUG-003关闭均未执行。

<a id="v35-f"></a>
## 14. 2026-08-31 V3-5 F验收

证据：[冻结协议](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-f/v35-f-validation-m2-gpu-20260831/protocol-completed.json)、[逐题结果](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-f/v35-f-validation-m2-gpu-20260831/per-question.jsonl)、[机器汇总](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-f/v35-f-validation-m2-gpu-20260831/phase-f-summary.json)、[解释结论](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-f/v35-f-validation-m2-gpu-20260831/phase-f-conclusion.md)、[最终核验](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-f/v35-f-validation-m2-gpu-20260831/phase-f-final-verification.json)及[JUnit](../../eval/crud_rag_1000/v3_1/.validation/v35-f-tests-20260831.xml)。

| 检查 | 实际结果 | 结论 |
|---|---|---|
| 范围/冻结 | validation 24题（20+4）；M2、Top10/配额2、Top5/配额2及原始Hybrid基线预先固定 | 通过；frozen_test未读取，结果后不调参 |
| 第一次启动 | CPU版`.venv`在GPU预热时被防降级保护拒绝 | 0正式检索/0评分；失败目录保留，不计质量结果 |
| 正式执行 | `.venv-gpu`；候选前/原始基线/候选后各24题，72正式查询+1预热，wall 35.46s | 通过；两模型cuda:0，候选首尾24/24逐阶段一致 |
| M2门槛 | 1/20可回答误拒、1/4不可回答拦截、0/24扰动翻转 | 不通过；要求0/20且至少3/4 |
| Top10/配额2 | gate off：Recall 0.9583、all-gold 18/20、Evidence 0.9409 | 不通过；低于原始基线且2道可回答回归 |
| Top5/配额2 | gate off：Recall 0.9417、all-gold 18/20、Evidence 0.9243 | 不通过；低于原始基线且2道可回答回归 |
| 原始基线 | Hybrid15/R15、Top5/配额3：Recall 0.9667、all-gold 19/20、Evidence 0.9667 | 比较锚点，不是新候选 |
| 选择 | 两候选eligible均false；`not_adopted`，selected=null | 通过协议；不自动进入G/S7 |
| 隔离 | quick_check=ok；1000 documents/units、2914 embeddings；封存37、生产39文件不变 | 通过 |
| 回归 | 164通过、0失败、0错误、3个依赖弃用警告 | 通过 |

执行状态`passed`表示协议、GPU、隔离和产物验收完成，不表示门控效果通过；最终核验明确`gate_passed=false`。误拒题为`crud-qa-3docs-64fa9b2fb82641eb8ecc0081`，三道不可回答被漏放。两个上下文候选共同回归`crud-qa-1doc-64fa9b2cb82641eb8ecbeeba`和`crud-qa-2docs-64fa9b2cb82641eb8ecbf007`。由于validation此前已被查看且不可回答仅4题，本结果仍不是正式盲测总体准确率，但足以按预注册规则否决当前候选。

## 关联文档

- 关系：缺陷验证；[BUG-002 结构化引用指标版本误判](../测试/BUG-002-结构化引用指标版本误判.md)
- 关系：关联引用；[DES-007 评测隔离与数据契约](../设计/DES-007-评测隔离与数据契约.md)
- 关系：验证对象；[DEV-011 评测CLI与数据集](../开发/DEV-011-评测CLI与数据集.md)
- 关系：依赖审核完成；[OPT-003 候选深度与拒答门控](../优化/OPT-003-候选深度与拒答门控.md)
- 关系：关联引用；[REQ-005 可复现评测与数据审核](../需求/REQ-005-可复现评测与数据审核.md)
- 关系：关联引用；[TEST-002 检索质量与性能](../测试/TEST-002-检索质量与性能.md)
- 关系：关联引用；[TEST-007 缺陷与历史反馈追踪](../测试/TEST-007-缺陷与历史反馈追踪.md)

## 变更记录

| 日期 | 内容 | 原因 |
|---|---|---|
| 2026-08-31 | 新增F固定M2的validation GPU验证、CPU环境失败保留、不采用结果及164项回归验收 | 完成用户授权的F阶段，明确区分执行通过与效果不通过 |
| 2026-08-31 | 新增E离线16策略、扰动稳定性、M2选择、失败替代run和145项回归验收 | 完成用户授权的E阶段并停在F之前 |
| 2026-08-31 | 新增补充B2 14配置GPU坐标扫描、因果/FP16/Top5门禁、隔离及134项回归验收 | 完成用户确认的Hybrid BM25参数调优并停在B2结论 |
| 2026-08-30 | 新增D真实重排与上下文预算、双control、元数据补齐重放、隔离及101项回归验收 | 完成用户授权的D阶段并停止在阶段边界 |
| 2026-08-29 | 新增C Dense K/阈值真实GPU扫描、双control、Trace语义、隔离及93项回归验收 | 完成质量优先C阶段并停止在阶段边界 |
| 2026-08-29 | 新增B六配置真实GPU消融、control稳定、隔离及86项回归验收 | 完成用户授权的B阶段并停止在阶段边界 |
| 2026-08-29 | 增加Dense索引状态、跨进程波动、精确oracle和完整链路补充验收 | 区分A质量通过与ANN微比较不可复现两个结论 |
| 2026-08-29 | 新增V3-5 A隔离、GPU、等价、缓存与回归验收 | A已完成并按用户要求停止在阶段边界 |
| 2026-08-28 | 接入120题GPU基线和71项测试证据，补V3-5数据门禁 | 修正无新版分数的过时状态，保留draft与非盲测边界 |
| 2026-08-27 | 从当前代码与原资料整理，补充证据边界和双向链接 | 建立独立new_docs对照体系，保留原文件 |
