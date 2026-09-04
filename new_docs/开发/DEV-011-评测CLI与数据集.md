# DEV-011 评测CLI与数据集

## 文档信息

- 文档ID：DEV-011
- 文档类型：开发
- 状态：已完成
- 创建日期：2026-08-27
- 最后更新：2026-08-31
- 证据口径：V3-5 A—F及补充B2已实现；F最终相关回归164项通过，但固定M2和两个候选均被validation否决，G与业务门控未实现。
- 导航：[本类目录](./README.md) · [文档中心](../README.md)

## 1. 目标与来源

关系：实现[DES-007 评测隔离与数据契约](../设计/DES-007-评测隔离与数据契约.md)和[REQ-005 可复现评测与数据审核](../需求/REQ-005-可复现评测与数据审核.md)，把数据契约、隔离运行、逐题指标与报告输出连接起来。

## 2. 代码与工具

| 代码 | 职责 |
|---|---|
| [eval.py](../../eval/eval.py) | run/compare/threshold-sweep/retrieval-sweep/hybrid-ablation |
| [dataset_v3.py](../../eval/dataset_v3.py) / [dataset_runtime.py](../../eval/dataset_runtime.py) | 校验、审核包和对话展平 |
| [adapters.py](../../eval/adapters.py) | 隔离适配和入库 |
| [metrics.py](../../eval/metrics.py) / [report.py](../../eval/report.py) | 文档、证据、答案、引用及报告 |
| [v3基线入口](../../eval/crud_rag_1000/v3/run_retrieval_baseline.py) | 120题CPU/GPU检索运行 |
| [v3.1构建](../../eval/crud_rag_1000/v3_1/build_revision.py) / [新版基线入口](../../eval/crud_rag_1000/v3_1/run_retrieval_baseline.py) | 独立修订及dry-run/后续运行 |
| [固定基线审计入口](../../eval/crud_rag_1000/v3_1/execute_frozen_baseline.py) / [结果验收与对照](../../eval/crud_rag_1000/v3_1/analyze_frozen_baseline.py) | 2026-08-28新增：隐式参数显式固定、保护hash/GPU采样、纯函数重算及产物封存 |
| [V3-5调参入口](../../eval/crud_rag_1000/v3_1/tuning_runner.py) | prepare复制校验、register-config强类型注册、development真实run/断点resume及A只读验收 |
| [Dense波动诊断探针](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/diagnostics/dense_variance_probe.py) | 独立进程重复Embedding/ANN、固定向量ANN、精确余弦oracle、全新索引生命周期及完整Hybrid链路对照 |
| [B模式消融入口](../../eval/crud_rag_1000/v3_1/run_phase_b_mode_ablation.py) / [入口回归](../../eval/crud_rag_1000/v3_1/test_phase_b_mode_ablation.py) | 同一GPU进程运行Dense/BM25/Hybrid×重排开关六配置，首尾control稳定性、逐行配置、隔离/GPU/API门禁及报告 |
| [C Dense扫描入口](../../eval/crud_rag_1000/v3_1/run_phase_c_dense_sweep.py) / [入口回归](../../eval/crud_rag_1000/v3_1/test_phase_c_dense_sweep.py) | 同一GPU进程运行Dense K/距离阈值质量扫描，双control、真实Trace语义、隔离/GPU/API门禁及确定性选择 |
| [D重排/上下文入口](../../eval/crud_rag_1000/v3_1/run_phase_d_rerank_context_sweep.py) / [入口回归](../../eval/crud_rag_1000/v3_1/test_phase_d_rerank_context_sweep.py) | 真实Dense/Reranker耦合预算扫描、完整metadata配额重放、质量/成本双候选及隔离/GPU/API门禁 |

<a id="implementation"></a>
## 3. 实际开发逻辑

CLI先设置隔离路径再导入backend；读取指定数据集/manifest，选择legacy或结构化Adapter，生成独立索引及逐题结果。Dataset v3对话评测展平turn，下游检索使用gold standalone_question，因此不能直接作为真实追问改写质量的结论。

正式运行要求strict review；--allow-draft-v3明确降级为preflight。run可能调用LLM；比较已有报告通常不需要推理。不得在文档整理时顺带运行完整评测。

v3.1复用原corpus和manifest，只修10题，保留110题；更换5个ID、7道查询文本，review仍draft。固定基线入口写v3_1/runs，从空库建新隔离索引，不是自动复用旧向量。2026-08-28已完成新版120题GPU固定基线；该事实不表示后续调参入口已完成。

<a id="bug-fixes"></a>
## 4. Bug修复记录

[BUG-002 结构化引用指标版本误判](../测试/BUG-002-结构化引用指标版本误判.md)：旧metrics按parser_version==structured-v1判断结构化引用，导致v2 MD/PPTX被误判。当前按unit_id、location_label、locator_kind等结构字段判定，并保留legacy路径；修复位置[metrics.py](../../eval/metrics.py)，回归[test_eval_metrics.py](../../tests/test_eval_metrics.py)。这修正指标有效性，不代表回答本身变得更准确。

## 5. 验证与后续

当前入口、版本报告与代码已核对，测试/证据见[TEST-006 数据集与评测验收](../测试/TEST-006-数据集与评测验收.md)。2026-08-27修题验收26项通过；2026-08-28固定基线前后同组71项通过，非142个不同测试。当前文档更新不再次运行产品测试。
审核、固定基线、V3-5校准和S7实现相互独立，不能自动越过门禁。

<a id="tuning"></a>
## 6. V3-5 A实验入口实现与结果

来源[DES-007实验合同](../设计/DES-007-评测隔离与数据契约.md#tuning)，实施顺序[OPT-003](../优化/OPT-003-候选深度与拒答门控.md#plan)。以下完成A实现；B/C/D/B2/E/F实现另见第8—13节，G和S7仍不能从本文件“已完成”状态外推。

1. 新实验目录、protocol和稳定config_id绑定题库/索引/检索代码/环境/split/题目/配置及派生fusion池；所有写入拒绝覆盖。
2. 完整复制封存Chroma/metadata，prepare前后逐字节相等；运行后验证不可变文件、quick_check及1000文档/2914 embeddings，不连接生产数据。
3. `retrieve_only`新增可选`runtime_config`并保持旧调用形状；参数通过现有retriever实际生效，Trace 72/72相符。
4. 精确结果缓存支持同配置逐题断点续算，缓存键任一依赖变化即拒绝；跨配置query/candidate/pair-score缓存未证明等价，明确关闭。
5. `run`在A只允许development，validation/frozen_test明确拒绝；`register-config`只登记强类型候选，不在A执行候选扫描。
6. 审计钩子拒绝出站IP，LLM_ENABLED=false且LLM模块保持未导入；两模型真实cuda:0状态写入summary。
7. 初版严格对照发现2道不可回答ANN候选差异并保留失败summary；`accept-a`按“可回答60/60顺序、质量、参数必须一致”验收，另写新证据而不覆盖失败记录。

实际实验数据在`eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/`。真实72题运行73.801s，其中首题冷启动52.857s、暖态mean 0.2575s、P50 0.258s、P95 0.284s；这些是A能力对照，不是参数优化或F阶段稳定性能结论。精确缓存重放4.104s，不作为检索延迟。

测试：先因pytest临时目录不在v3_1得到81通过/1准备约束失败；改用`v3_1/.validation`后同组82项通过，再补Chroma运行簿记边界回归，最终83项通过。最终JUnit为[v35-a-final2-tests-20260829.xml](../../eval/crud_rag_1000/v3_1/.validation/v35-a-final2-tests-20260829.xml)。

## 7. Dense ANN稳定性诊断实现

2026-08-29在A目录新增只读/隔离诊断探针；它不接生产库、不修改业务检索算法，也不调用生成模型。探针按题保存查询Embedding的float32哈希、Chroma Top15、固定向量重复ANN、2914向量精确余弦Top15和数据库序列状态；可选择新建隔离索引，并可经现有`retrieve_with_trace`执行完整Hybrid+Reranker链路。

第一次误用CPU版`.venv`时，严格设备检查按设计直接失败，没有回退CPU，也没有把该次计入结果。正式数据全部由`.venv-gpu`产生，PyTorch CUDA可用，Embedding与Reranker实际设备均为`cuda:0`。最终共覆盖15个独立Dense进程、每类各1000次Embedding+ANN与固定向量ANN，以及5个进程150次完整链路；同进程重复稳定，跨进程候选存在有限变化。

机器结论见[dense-variance-analysis.json](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/diagnostics/dense-variance-analysis.json)，解释与边界见[Dense差异来源与波动范围](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/diagnostics/Dense差异来源与波动范围-20260829.md)。该探针不是参数扫描入口；B已据此把运行次序和首尾control写入每个配置产物。

<a id="tuning-b"></a>
## 8. V3-5 B模式消融实现

新入口只接受已通过A且Dense诊断存在的实验目录，新建`phase-b/<run_id>/`，拒绝覆盖。它验证题库hash、development 72 ID、复制索引契约和六个强类型配置；六配置只改变mode/reranker_enabled，config_id必须唯一。

运行时一次导入Adapter并加载GPU模型，先预热1题，再按control→Dense重排→BM25重排→Hybrid无重排→Dense无重排→BM25无重排→control的顺序执行七轮。每道题写独立JSONL并立即刷新，Trace effective_config必须逐行相等；每轮用现有`build_candidate_result`生成标准质量、分阶段gold和延迟。末尾要求control所有阶段72/72一致、两模型actual cuda:0、LLM未导入、无出站连接、存储逻辑契约不变，否则拒绝生成通过结论。

2026-08-29实际完成505次检索，wall 110.237s。原始[机器汇总](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-b/v35-b-mode-ablation-20260829/phase-b-summary.json)、[逐题报告](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-b/v35-b-mode-ablation-20260829/phase-b-report.md)、[解释结论](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-b/v35-b-mode-ablation-20260829/phase-b-conclusion.md)和[最终核验](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-b/v35-b-mode-ablation-20260829/phase-b-final-verification.json)均保留。最终相关测试86项通过；B没有实现C参数网格、门控或生产设置修改。

<a id="tuning-c"></a>
## 9. V3-5 C Dense候选深度与阈值扫描实现

新入口要求A最终验收、Dense诊断及B最终核验均通过，并在B实验的兄弟目录新建`phase-c/<run_id>/`，拒绝覆盖。它锁定development 72题、1000篇/2914向量的同一隔离索引和Dense+Reranker路径，固定Reranker K15、最终Top5、每文档3块；先运行K10/15/30/50，再以“无硬质量回归、质量优先、相同质量取更小K”的确定性规则选K15，随后运行0.55/0.60/0.65/0.67/0.70/0.75阈值。

运行器共安排12轮并只加载一次GPU模型：Hybrid control前置、Dense control前置、3个非基线K、5个非基线阈值、Dense control后置、Hybrid control后置。每行校验effective config及真实Trace：Dense原始数量与eligible标记、阈值、Reranker输入集合和分数、selected结果、阶段与总计时；Dense单路的`fusion_pool_k`明确为不适用。两组control不仅比最终ID，还逐项比较rank、distance、eligible/filter、Reranker score，72/72均精确一致。

2026-08-29实际完成864次正式检索和1次预热，wall 234.99s。原始[机器汇总](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-c/v35-c-dense-quality-20260829/phase-c-summary.json)、[逐题报告](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-c/v35-c-dense-quality-20260829/phase-c-report.md)、[解释结论](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-c/v35-c-dense-quality-20260829/phase-c-conclusion.md)和[最终核验](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-c/v35-c-dense-quality-20260829/phase-c-final-verification.json)均保留。最终选择仍为Dense K15/0.67，没有修改生产设置。首次全组测试因默认pytest临时目录超出v3_1而得到92通过/1个准备约束失败；使用新的受控`--basetemp`重跑同一93项全部通过，两个JUnit均保留以避免隐藏环境型失败。

<a id="tuning-d"></a>
## 10. V3-5 D重排预算与最终上下文实现

D入口要求C最终验收通过并确认Dense K15/distance 0.67结论，在新的`phase-d/<run_id>/`拒绝覆盖执行。真实矩阵包含9个Dense/Reranker配置：K15/R10降本、K20/30/50前级锚点及K20/30/50真实重排输入。Reranker K大于前级候选时不会假装生效；每行验证实际输入数和集合。

Trace chunk摘要缺少`document_id`、unit和offset，不能直接支撑精确Evidence重放。因此入口从隔离Chroma一次性回查2914块完整metadata，把Reranker顺序/分数与完整内容合并，再使用与业务`_limit_per_document`等价的逻辑重放15个合法TopK/配额组合。每个真实配置先逐题证明重放selected与业务返回一致；旧Trace的selected标记不参与新配额选择。

2026-08-30完成12轮864次正式检索和1次预热，wall 346.08s。原始[机器汇总](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-d/v35-d-rerank-context-quality-20260830/phase-d-summary.json)、[解释结论](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-d/v35-d-rerank-context-quality-20260830/phase-d-conclusion.md)和[最终核验](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-d/v35-d-rerank-context-quality-20260830/phase-d-final-verification.json)均保留。最终产生Dense20/Reranker20、Top10/doc cap2质量候选及Top5/doc cap2成本候选；未修改生产设置。101项相关测试通过、2个依赖弃用警告。

<a id="tuning-b2"></a>
## 11. V3-5补充B2 Hybrid BM25/RRF扫描实现

新增[运行入口](../../eval/crud_rag_1000/v3_1/run_phase_b2_hybrid_bm25_sweep.py)及[入口回归](../../eval/crud_rag_1000/v3_1/test_phase_b2_hybrid_bm25_sweep.py)。入口要求D最终验证通过，在`phase-b2/<run_id>/`拒绝覆盖执行，只允许development；固定Dense20/Reranker20、distance 0.67、Top5和每文档3块，按BM25 TopK→k1→b→RRF的坐标顺序搜索14个唯一Hybrid配置。k1/b变化先直接预热对应BM25索引，重建耗时排除在质量与检索时延之外；BM25和RRF在CPU，Embedding和Reranker核验cuda:0。

每行验证真实effective config、BM25正分/顺序/稳定同分规则、RRF双路重算、Dense分支与同期control一致及重排输入。跨配置门禁要求BM25 TopK前缀、单改RRF时上游不变；FP16共享重排分数最大绝对差不得超过0.02，相同Reranker候选集的最终Top5必须完全一致，其他排序反转只能在两次打分都完全同分时发生。首次启动运行在只读审计发现因果检查不全后主动中止；第二次17轮完成但被临时1e-6容差拒绝，两份目录均以`aborted.json`保留，未复用为有效结论。

2026-08-31用新run id完成17轮、1224次正式检索和1次预热，wall 462.591s。[机器汇总](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-b2/v35-b2-hybrid-bm25-fp16guard-20260831/phase-b2-summary.json)、[报告](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-b2/v35-b2-hybrid-bm25-fp16guard-20260831/phase-b2-report.md)、[解释结论](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-b2/v35-b2-hybrid-bm25-fp16guard-20260831/phase-b2-conclusion.md)及[最终核验](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-b2/v35-b2-hybrid-bm25-fp16guard-20260831/phase-b2-final-verification.json)均通过。最佳Hybrid K50/k1=1.5/b=0.75/RRF60只与Dense平局，故不推广、不复查D；[JUnit](../../eval/crud_rag_1000/v3_1/.validation/v35-b2-final6-tests-20260831.xml)为134通过、0失败、0错误和2个依赖弃用警告。

<a id="tuning-e"></a>
## 12. V3-5 E离线证据充分性门控实现

新增[离线入口](../../eval/crud_rag_1000/v3_1/run_phase_e_evidence_gate.py)及[入口回归](../../eval/crud_rag_1000/v3_1/test_phase_e_evidence_gate.py)。`run`子命令要求D最终验收、Dense20/Reranker20真实选择及Top10/配额2、Top5/配额2候选完全匹配；只读取development 72题，写入新的`phase-e/<run_id>/`，拒绝覆盖。入口逐题提取Reranker Top1/Top2/Top3、margin、Top3均值、Top5支持计数、Dense eligible最佳距离和Reranker Top1的Dense rank/distance；首轮决策只使用Top1与两项Dense距离，避免把诊断字段变成额外过拟合自由度。

策略矩阵固定为关闭门控、7个单阈值和M1—M8，共16项。每个策略逐题执行8个扰动角：Top1 ±0.02、两项Dense distance各±0.005；严格资格要求0误拒、至少9/12拦截且72题决策均稳定。平局先比较拦截数，再偏好更保守的Dense弱阈值和预注册中心low/high，不使用加权分。所选策略应用到两个D重放上下文，拒答题的离线retrieval置空，原行和D文件不修改；逐题JSONL保留所有策略决定、原因、稳定性以及门控前后chunk ID。

有效run为`phase-e/v35-e-evidence-gate-guard-20260831/`：[机器汇总](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-e/v35-e-evidence-gate-guard-20260831/phase-e-summary.json)、[报告](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-e/v35-e-evidence-gate-guard-20260831/phase-e-report.md)、[结论](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-e/v35-e-evidence-gate-guard-20260831/phase-e-conclusion.md)及[最终核验](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-e/v35-e-evidence-gate-guard-20260831/phase-e-final-verification.json)。M2入选，0/60误拒、11/12拦截、0扰动翻转；M8有2题翻转。145项相关回归通过、2个依赖弃用警告。首次run在最终验收读取存储统计时使用错误字段层级，失败记录保留且`quality_result_valid=false`；修正后使用新run id重跑，没有覆盖原证据。

<a id="tuning-f"></a>
## 13. V3-5 F validation固定验证实现

新增[运行入口](../../eval/crud_rag_1000/v3_1/run_phase_f_validation.py)及[入口回归](../../eval/crud_rag_1000/v3_1/test_phase_f_validation.py)。`run`要求D/E最终核验通过且E明确选中M2及四个固定参数；只读取A协议中的validation 24 ID。入口在写入`protocol.json`后才加载模型，正式调度候选前置、原始基线、候选后置各24题。候选固定Dense20/Reranker20、Top10/配额2；Top5/配额2从候选Reranker Trace回查2914块完整metadata后重放。原始基线固定Hybrid15/Reranker15、Top5/配额3、门控关闭。门控开关使用同一检索结果离线置空，业务`refusal_gate.available/enabled`仍保持false。

每行验证effective config、Dense阈值资格、Reranker输入、selected与本地重放；候选前后对Dense raw/eligible、Reranker、selected逐项核对。M2继续使用E的8角扰动，F资格固定为0/20误拒、至少3/4拦截、0翻转、all-gold至少19/20，并要求硬质量不低于原始基线且无逐题回归/混合。选择器不做加权：只有Top10至少一项有序质量严格优于Top5且无任何下降才取Top10，否则质量平局取Top5；无合格项直接输出`not_adopted`。

第一次用CPU版`.venv`启动时，Embedding防降级保护在预热前拒绝运行；[失败记录](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-f/v35-f-validation-m2-20260831/aborted.json)证明正式检索0、评分0。用户确认后改用`.venv-gpu`和新run id，完成72次正式检索+1预热，wall 35.46s；候选首尾24/24稳定、两模型cuda:0、无LLM/出站连接。有效[机器汇总](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-f/v35-f-validation-m2-gpu-20260831/phase-f-summary.json)、[报告](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-f/v35-f-validation-m2-gpu-20260831/phase-f-report.md)、[结论](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-f/v35-f-validation-m2-gpu-20260831/phase-f-conclusion.md)及[最终核验](../../eval/crud_rag_1000/v3_1/tuning/v35-a-baseline-20260829/phase-f/v35-f-validation-m2-gpu-20260831/phase-f-final-verification.json)均保留。执行验收通过但策略资格失败：1/20误拒、1/4拦截、两个候选18/20 all-gold并各有2道回归；[JUnit](../../eval/crud_rag_1000/v3_1/.validation/v35-f-tests-20260831.xml)为164通过、0失败、0错误和3个依赖弃用警告。因此不采用、不进入G、不改生产设置。

## 关联文档

- 关系：调参入口与执行边界；[OPT-003 候选深度与拒答门控](../优化/OPT-003-候选深度与拒答门控.md#plan)
- 关系：修复缺陷；[BUG-002 结构化引用指标版本误判](../测试/BUG-002-结构化引用指标版本误判.md)
- 关系：来源设计；[DES-007 评测隔离与数据契约](../设计/DES-007-评测隔离与数据契约.md)
- 关系：关联引用；[DEV-006 来源引用与结构预览](../开发/DEV-006-来源引用与结构预览.md)
- 关系：关联依赖；[REQ-005 可复现评测与数据审核](../需求/REQ-005-可复现评测与数据审核.md)
- 关系：测试验证；[TEST-006 数据集与评测验收](../测试/TEST-006-数据集与评测验收.md)
- 关系：测试验证；[TEST-007 缺陷与历史反馈追踪](../测试/TEST-007-缺陷与历史反馈追踪.md)

## 变更记录

| 日期 | 内容 | 原因 |
|---|---|---|
| 2026-08-31 | 新增F固定M2、validation真实GPU检索、双上下文重放、门控消融及164项回归 | 用户授权执行F并确认CPU环境失败后改用GPU环境新run重试；结果不达标即停止 |
| 2026-08-31 | 新增E离线16策略、逐题门控/扰动、上下文绑定、最终验收及145项回归 | 用户授权执行E，要求完成后停在阶段边界 |
| 2026-08-31 | 新增补充B2 Hybrid BM25/RRF坐标扫描、跨配置因果门禁、FP16/Top5双层校验及134项回归 | 用户确认补充BM25参数调优并批准修正后正式重跑 |
| 2026-08-30 | 新增D真实重排预算、完整metadata上下文重放、双control及101项回归 | 用户授权执行D并要求阶段完成后同步文档 |
| 2026-08-29 | 新增C同进程Dense K/距离阈值扫描、Trace语义核验、双control及93项最终回归 | 用户选择质量优先并授权执行C，要求阶段完成即停 |
| 2026-08-29 | 新增B同进程六配置GPU消融入口、逐题产物和86项最终回归 | 用户授权在量化ANN噪声后继续B并要求阶段完成即停 |
| 2026-08-29 | 新增并实跑Dense跨进程、固定向量、精确oracle和全新索引生命周期探针 | 确认A阶段候选差异来源与经验波动范围 |
| 2026-08-29 | 完成A调参入口、适配层显式配置、隔离副本、缓存及72题GPU验收 | 用户要求从A开始并在每阶段后停止 |
| 2026-08-28 | 同步固定GPU基线审计入口与71项测试；增加V3-5 A待办 | 区分已经运行的固定基线与尚未开发的多参数实验工具 |
| 2026-08-27 | 从当前代码与原资料整理，补充证据边界和双向链接 | 建立独立new_docs对照体系，保留原文件 |
