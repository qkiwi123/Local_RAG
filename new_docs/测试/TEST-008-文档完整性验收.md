# TEST-008 文档完整性验收

## 文档信息

- 文档ID：TEST-008
- 文档类型：测试
- 状态：已完成
- 创建日期：2026-08-27
- 最后更新：2026-09-03
- 证据口径：分别记录首次整理及后续日常维护检查；静态文档检查与V3-5真实产品验收、BM25实现验证分开。
- 导航：[本类目录](./README.md) · [文档中心](../README.md)

## 1. 验收目标与对象

关系：验证[DEV-013 文档检查工具](../开发/DEV-013-文档检查工具.md)及[文档治理验收条件](../需求/REQ-007-文档治理与追踪.md#acceptance)。首次整理确保仅新增new_docs；后续维护检查文档结构、导航和证据边界，不要求真实代码永久保持旧快照。

## 2. 环境与范围

2026-08-27，Windows PowerShell，仓库根目录；HEAD为33dac4e8a548f3c4cbd2de5ec6949840ff195fe2，分支feature/rag-v2-optimization。整理前已有未提交修改，原状态与1427个文件hash保存在[源快照](../工具/source_snapshot.json)。

测试只针对文档和检查器，不运行pytest业务套件、浏览器、构建、索引或模型。原生apply_patch已通过创建→更新→读取→删除探针。

<a id="cases"></a>
## 3. 2026-08-27整理验收用例

| 用例 | 输入 | 预期 | 实际结果 |
|---|---|---|---|
| D01 | 全部核心文档 | ID、元信息、状态和日期合法 | 已通过 |
| D02 | 全部Markdown链接 | 相对目标及锚点可解析 | 已通过 |
| D03 | 核心关系图 | 必要上游、双向边和无孤立节点 | 已通过 |
| D04 | 六类README与总入口 | 分类覆盖全部核心文档 | 已通过 |
| D05 | 源快照 | 原文件hash全部一致 | 已通过 |
| D06 | 故意损坏的内存副本 | 检查器能识别坏ID/链接/关系等 | 已通过 |
| D07 | 旧docs清单 | 24份原文逐项映射 | 已通过 |

<a id="results"></a>
## 4. 2026-08-27整理验收结果

最终检查报告：[validation-report.json](../工具/validation-report.json)。本轮已执行静态检查、检查器自检、源文件hash比对、Git状态对照与Python语法解析。

| 检查项 | 结果 |
|---|---|
| Markdown / 核心文档 | 58 / 48；ID唯一、元信息/日期/状态/命名合法 |
| 关系网络 | 177组双向核心关系，无孤立文档，整体连通 |
| 相对链接/章节锚点/README | 全部通过；具体数量以JSON报告为准 |
| 原docs映射 | 24/24 |
| 原文件保护 | 1427/1427 hash一致 |
| 整理前Git状态 | 除新增new_docs外完全一致，原未提交修改保留 |
| 检查器 | 11项内存自检通过；Python AST解析通过 |
| UTF-8与空白 | 无替换字符、未展开占位链接或行尾空白 |

首轮检查暴露并修正了两点：检查器需要忽略模板围栏中的同名关联标题；文档治理链需要与项目架构建立显式双向关系。修正后重跑通过，没有把首次失败隐去或当作业务测试失败。

已完成仅指本轮文档整理与静态验证；新体系是否正式替代旧入口仍由用户对照后决定。

## 5. 首次整理的验收命令

```powershell
.\.venv\Scripts\python.exe new_docs\工具\check_docs.py --check-preserved --self-test --report new_docs\工具\validation-report.json
```

报告仅保存检查结果和文件hash比对状态，不包含原文或密钥明文。未来真实代码变化后不应要求继续通过本次保护hash；日常仅运行不带--check-preserved的检查。

## 6. 首次整理的未验证事项

产品全量测试、生产库schema/内容、服务端口、模型权重与外部模型、恢复演练均未验证；它们不是本次文档交付的通过项。原文历史测试记录保持原日期。

<a id="maintenance-v35"></a>
## 7. 2026-08-28 V3-5方案维护验收

本次只更新new_docs：将此前已完成的v3.1 GPU基线接入生命周期文档，并补充V3-5实施方案、实验契约、待开发事项和阶段验收边界。
没有修改或重跑封存题库/基线，没有执行参数扫描、门控实现或生产默认值变更。

本次报告：[validation-report-20260828-v35-plan.json](../工具/validation-report-20260828-v35-plan.json)。历史validation-report.json及source_snapshot.json保留，不覆盖首次整理证据。

| 检查项 | 本次结果 |
|---|---|
| Markdown / 核心文档 | 58 / 48，静态检查通过 |
| 关系网络 | 180组双向核心关系，无孤立文档，整体连通 |
| 链接、锚点和导航 | 全部通过；链接数量以本次JSON为准 |
| 原docs映射 | 24/24 |
| 检查器内存自检 | 11/11通过 |
| 原文件保护快照 | 本次不执行；source_files_checked=0，不宣称重新验证1427个历史hash |
| 业务测试 / 模型推理 / 调参 | 本轮未执行，之前的基线运行证据另见对应测试文档 |

按[日常维护流程](../运维/OPS-004-文档维护与变更流程.md)，本次不使用仅针对首次整理的--check-preserved，也不更新旧快照以掩盖后续真实代码变化。

```powershell
.\.venv\Scripts\python.exe -B new_docs\工具\check_docs.py --self-test --report new_docs\工具\validation-report-20260828-v35-plan.json
```

本次通过只说明文档结构、自检和导航有效，不代表V3-5已实施或获得新的检索、拒答、时延指标。

<a id="maintenance-v35-a"></a>
## 8. 2026-08-29 V3-5 A文档维护验收

本次在A真实实现、GPU运行、回归及Dense差异补充诊断完成后，同步DES-007、DEV-011、TEST-002/006、OPT-003及导航。产品结果由[TEST-006 A验收](./TEST-006-数据集与评测验收.md#v35-a)和[Dense补充验收](./TEST-006-数据集与评测验收.md#dense-variance)承载，本节只验收文档结构。

报告：[validation-report-20260829-v35-a.json](../工具/validation-report-20260829-v35-a.json)。最新检查结果为58份Markdown、48份核心文档、1478个链接、180组双向核心关系、24/24旧文档映射和11/11检查器自检，错误为0；`source_files_checked=0`，未冒充重新验证首次整理的1427个历史hash。

```powershell
.\.venv\Scripts\python.exe -B new_docs\工具\check_docs.py --self-test --report new_docs\工具\validation-report-20260829-v35-a.json
```

该通过项不替代83项pytest、72题GPU运行、数据库检查或artifact manifest核验；这些证据均保留在实验和TEST-006中。

<a id="maintenance-v35-b"></a>
## 9. 2026-08-29 V3-5 B文档维护验收

本次在B六配置GPU模式消融及86项产品回归完成后，同步REQ-005、DES-007、DEV-011、TEST-002/006、OPT-003和各分类/总导航。产品证据由[TEST-006 B验收](./TEST-006-数据集与评测验收.md#v35-b)承载，本节只验收文档结构。

报告：[validation-report-20260829-v35-b.json](../工具/validation-report-20260829-v35-b.json)。检查结果为58份Markdown、48份核心文档、1498个链接、180组双向核心关系、24/24旧文档映射和11/11检查器自检，错误为0。`source_files_checked=0`，不覆盖首次整理的历史保护快照。

```powershell
.\.venv\Scripts\python.exe -B new_docs\工具\check_docs.py --self-test --report new_docs\工具\validation-report-20260829-v35-b.json
```

该静态通过项不替代505次GPU检索、control逐阶段稳定性、数据库/生产快照/封存产物检查或86项pytest；这些证据保存在B实验目录和TEST-006中。

<a id="maintenance-v35-c"></a>
## 10. 2026-08-29 V3-5 C文档维护验收

本次在C Dense候选深度/阈值GPU扫描及93项产品回归完成后，同步REQ-005、DES-007、DEV-011、TEST-002/006、OPT-003和各分类/总导航。产品证据由[TEST-006 C验收](./TEST-006-数据集与评测验收.md#v35-c)承载，本节只验收文档结构。

报告：[validation-report-20260829-v35-c.json](../工具/validation-report-20260829-v35-c.json)。检查结果为58份Markdown、48份核心文档、1520个链接、360条有向/180组双向核心关系、24/24旧文档映射和11/11检查器自检，错误为0。`source_files_checked=0`，不覆盖首次整理的历史保护快照。

```powershell
.\.venv\Scripts\python.exe -B new_docs\工具\check_docs.py --self-test --report new_docs\工具\validation-report-20260829-v35-c.json
```

该静态通过项不替代865次GPU检索、Dense/Hybrid双control稳定性、数据库/生产快照/封存产物检查或93项pytest；这些证据保存在C实验目录和TEST-006中。

<a id="maintenance-v35-d"></a>
## 11. 2026-08-30 V3-5 D文档维护验收

本次在D重排/上下文预算GPU扫描及101项产品回归完成后，同步REQ-005、DES-007、DEV-011、TEST-002/006、OPT-003和各分类/总导航。产品证据由[TEST-006 D验收](./TEST-006-数据集与评测验收.md#v35-d)承载，本节只验收文档结构。

报告：[validation-report-20260830-v35-d.json](../工具/validation-report-20260830-v35-d.json)。检查结果为58份Markdown、48份核心文档、1538个链接、360条有向/180组双向核心关系、24/24旧文档映射和11/11检查器自检，错误为0。`source_files_checked=0`，不覆盖首次整理的历史保护快照。

```powershell
.\.venv\Scripts\python.exe -B new_docs\工具\check_docs.py --self-test --report new_docs\工具\validation-report-20260830-v35-d.json
```

该静态通过项不替代865次GPU检索、Dense/Hybrid双control稳定性、数据库/生产快照/封存产物检查或101项pytest；这些证据保存在D实验目录和TEST-006中。

<a id="maintenance-v35-b2"></a>
## 12. 2026-08-31 V3-5补充B2文档维护验收

本次在B2 Hybrid BM25/RRF坐标扫描、FP16/Top5因果门禁、134项产品回归及最终GPU验收完成后，同步REQ-005、DES-007、DEV-011、TEST-002/006、OPT-003和各分类/总导航。产品证据由[TEST-006 B2验收](./TEST-006-数据集与评测验收.md#v35-b2)承载，本节只验收文档结构。

报告：[validation-report-20260831-v35-b2.json](../工具/validation-report-20260831-v35-b2.json)。检查结果为58份Markdown、48份核心文档、1561个链接、360条有向/180组双向核心关系、24/24旧文档映射和11/11检查器自检，错误为0。`source_files_checked=0`，不覆盖首次整理的历史保护快照。

```powershell
.\.venv\Scripts\python.exe -B new_docs\工具\check_docs.py --self-test --report new_docs\工具\validation-report-20260831-v35-b2.json
```

该静态检查不替代1225次真实GPU检索、14配置/17轮因果门禁、数据库/生产快照/封存产物检查或134项pytest；这些证据保存在B2实验目录和TEST-006中。

<a id="maintenance-v35-e"></a>
## 13. 2026-08-31 V3-5 E文档维护验收

本次在E离线16策略、扰动门禁、M2选择、145项回归及最终验收完成后，同步REQ-005、DES-007、DEV-011、TEST-002/006、BUG-003、OPT-003和各分类/总导航。产品证据由[TEST-006 E验收](./TEST-006-数据集与评测验收.md#v35-e)承载，本节只验收文档结构。

报告：[validation-report-20260831-v35-e.json](../工具/validation-report-20260831-v35-e.json)。检查结果为58份Markdown、48份核心文档、1588个链接、360条有向/180组双向核心关系、24/24旧文档映射和11/11检查器自检，错误为0。`source_files_checked=0`，不覆盖首次整理的历史保护快照。

```powershell
.\.venv\Scripts\python.exe -B new_docs\工具\check_docs.py --self-test --report new_docs\工具\validation-report-20260831-v35-e.json
```

该静态检查不替代72题离线门控、16策略/8角扰动、D输入/生产快照/封存产物检查或145项pytest；这些证据保存在E实验目录和TEST-006中。

<a id="maintenance-v35-f"></a>
## 14. 2026-08-31 V3-5 F文档维护验收

本次在F固定M2的validation 24题GPU验证、CPU环境失败证据保留、164项产品回归和`not_adopted`结论完成后，同步REQ-005、DES-007、DEV-011、TEST-002/006、BUG-003、TEST-007、OPT-003及各分类/总导航。产品证据由[TEST-006 F验收](./TEST-006-数据集与评测验收.md#v35-f)承载，本节只验收文档结构。

报告：[validation-report-20260831-v35-f.json](../工具/validation-report-20260831-v35-f.json)。最终检查为58份Markdown、48份核心文档、1618个链接、360条有向/180组双向核心关系、24/24旧文档映射和11/11检查器自检，错误为0。`source_files_checked=0`，不覆盖首次整理的历史保护快照。

```powershell
.\.venv\Scripts\python.exe -B new_docs\工具\check_docs.py --self-test --report new_docs\工具\validation-report-20260831-v35-f.json
```

该静态检查不替代72次真实GPU检索、M2门控/两个上下文候选效果门槛、数据库/生产快照/封存产物检查或164项pytest；这些证据保存在F实验目录和TEST-006中。执行验收通过不代表策略效果通过。

<a id="maintenance-persistent-bm25"></a>
## 15. 2026-09-03 BM25持久化方案文档维护验收

本次新增[OPT-005 BM25持久化稀疏索引](../优化/OPT-005-BM25持久化稀疏索引.md)，并同步REQ-002、DES-003、DEV-002/004、TEST-002、OPS-002及总导航和六类导航。该方案固定了本地Chroma Sparse不可用的证据边界、BM25S候选、generation/manifest合同、过滤语义、分阶段停止点、性能门槛及成套回滚要求。

报告：[validation-report-20260903-bm25-persistence-plan.json](../工具/validation-report-20260903-bm25-persistence-plan.json)。最终检查为59份Markdown、49份核心文档、1668个链接、372条有向/186组双向核心关系、24/24旧文档映射和11/11检查器自检，错误为0；`source_files_checked=0`，不覆盖首次整理的历史保护快照。

```powershell
.\.venv\Scripts\python.exe -B new_docs\工具\check_docs.py --self-test --report new_docs\工具\validation-report-20260903-bm25-persistence-plan.json
```

该检查只验证文档结构、导航、链接和双向核心关系，不代表BM25S已经安装、排序等价性或1000篇/120题新旧性能对照已经完成，也不代表生产已切换。本轮已按用户决定取消1万篇/完整语料扩展；产品测试执行数为0，代码、依赖、Chroma、SQLite和现有索引不在修改范围。

## 16. 2026-09-03 BM25持久化P1结果文档维护验收

用户确认P1后，已将真实结果同步到OPT-005、REQ-002、DES-003、DEV-002/004、TEST-002、OPS-002及分类/总导航。文档明确区分：原生BM25S等价门禁失败、epsilon兼容IDF候选通过、隔离微基准、3轮优化前系统基线，以及P2/P3/P4仍待执行的业务接入与前后对照。

报告：[validation-report-20260903-bm25-p1.json](../工具/validation-report-20260903-bm25-p1.json)。检查为59份Markdown、49份核心文档、1678个链接、372条有向/186组双向核心关系、24/24旧文档映射和11/11检查器自检，错误为0；`source_files_checked=0`。

```powershell
.\.venv\Scripts\python.exe -B new_docs\工具\check_docs.py --self-test --report new_docs\工具\validation-report-20260903-bm25-p1.json
```

该静态检查不替代P1实际实验。产品证据分别保存在[等价性结果](../../eval/crud_rag_1000/v3_1/bm25_persistence_p1/runs/equivalence-20260903-r2/result.json)和[优化前基线修正版汇总](../../eval/crud_rag_1000/v3_1/bm25_persistence_p1/runs/old-baseline-20260903/summary-v2.json)；P1未修改业务代码、正式requirements或生产数据，也未授权P2。

## 17. 2026-09-03 BM25持久化P2结果文档维护验收

用户确认P2后，已同步持久化Sparse协议、纯分词模块、BM25S generation/manifest/mmap、活动指针、安全构建命令、双构建确定性、真实1000篇/120题oracle及最终回归结果。文档明确P2只完成隔离基础设施，现有Hybrid仍使用旧backend，P3业务接入和P4优化前后系统对照均未执行。

报告：[validation-report-20260903-bm25-p2.json](../工具/validation-report-20260903-bm25-p2.json)。最终静态检查为59份Markdown、49份核心文档、1692个链接、372条有向/186组双向核心关系、24/24旧文档映射和11/11检查器自检，错误为0；`source_files_checked=0`。

```powershell
.\.venv\Scripts\python.exe -B new_docs\工具\check_docs.py --self-test --report new_docs\工具\validation-report-20260903-bm25-p2.json
```

该静态检查不替代P2产品证据：最终代码的Sparse相关55项与全量742项Python回归通过、3项跳过；两次隔离generation各为2914块和4,261,289字节，120题K=1/5/15/50严格一致且最大分数差0。生产数据、业务backend、运行配置和Milvus均未改变。

## 18. 2026-09-03 BM25持久化P3结果文档维护验收

用户确认P3后，已同步部署级Sparse后端选择、固定全库mmap业务路径、批量Chroma回取、空/非空过滤语义、显式故障、Trace/运行状态和前端展示。真实1000篇/120题同Dense候选回放、专项/全量回归及P4停止点均已记录；生产默认配置和数据未切换。

报告：[validation-report-20260903-bm25-p3.json](../工具/validation-report-20260903-bm25-p3.json)。最终静态检查为59份Markdown、49份核心文档、1700个链接、372条有向/186组双向核心关系、24/24旧文档映射和11/11检查器自检，错误为0；`source_files_checked=0`。

```powershell
.\.venv\Scripts\python.exe -B new_docs\工具\check_docs.py --self-test --report new_docs\工具\validation-report-20260903-bm25-p3.json
```

该静态检查不替代P3产品证据：Sparse Top15、RRF、Reranker输入及最终Top5回放均120/120一致；健康路径全库扫描和内存BM25构建为0；相关95项、全量Python 759项和前端39项通过。分数最大差`2.8421709430404007e-13`已保留，P4性能与硬件对照尚未执行。

## 19. 2026-09-03 BM25持久化P4结果文档维护验收

用户确认P4后，已同步固定1000篇/2914块/120题、旧/新各3个独立GPU进程的同期对照。OPT-005、REQ-002、DES-003、DEV-004、TEST-002、OPS-002及分类/总导航均记录6次冷查询、720次暖态Hybrid、BM25排序与质量、阶段时间、CPU/RSS/Private Bytes、磁盘、GPU、mmap、并发读和句柄释放结果，并明确P5/P6未执行。

报告：[validation-report-20260903-bm25-p4.json](../工具/validation-report-20260903-bm25-p4.json)。最终静态检查为59份Markdown、49份核心文档、1712个链接、372条有向/186组双向核心关系、24/24旧文档映射和11/11检查器自检，错误为0；`source_files_checked=0`，不覆盖首次整理的历史保护快照。

```powershell
.\.venv\Scripts\python.exe -B new_docs\工具\check_docs.py --self-test --report new_docs\工具\validation-report-20260903-bm25-p4.json
```

该静态检查不替代P4产品证据。P4修正版门禁通过：BM25 K1/5/15/50六轮均120/120有序一致，Sparse暖态均值/P95下降72.80%/85.61%，完整暖态均值下降3.34%，GPU峰值不变，全量Python 759通过/3跳过。初始过严的全GPU进程排序门禁失败及Dense重复运行波动均保留，不把完整冷查询离群或约3.3%端到端观测夸大为生产结论。

## 关联文档

- 关系：关联引用；[DES-008 文档治理架构](../设计/DES-008-文档治理架构.md)
- 关系：验证对象；[DEV-013 文档检查工具](../开发/DEV-013-文档检查工具.md)
- 关系：持续维护；[OPS-004 文档维护与变更流程](../运维/OPS-004-文档维护与变更流程.md)
- 关系：关联引用；[REQ-007 文档治理与追踪](../需求/REQ-007-文档治理与追踪.md)

## 20. 2026-09-03 BM25 P5/P6确认方案文档维护验收

用户先提出采用新版BM25作为生产Sparse默认，随后明确要求先确认P5/P6具体内容、更新文档，确认后才实施。本轮仅更新OPT-005、REQ-002、DES-003、DEV-002/004、TEST-002、OPS-002及总导航、六类导航和工具导航；没有改业务代码、依赖、`.env`/`.env.example`、生产Chroma/SQLite或Sparse活动索引，也没有运行产品测试。

文档已固定P5为“条件采用”：只替换Hybrid内部Sparse backend，不改变当前`RETRIEVAL_MODE=hybrid`、Dense、RRF、Reranker、TopK、BM25参数或过滤语义。P6按A生命周期与隔离回归、B生产影子generation与成套恢复、C原子启用/观察/回滚三个停止点执行；写前持久标脏、跨重启dirty、后台合并重建、源变化废弃候选代、禁止静默降级及评测索引不得发布生产均为硬门禁。

本节只验收文档结构和方案边界，不代表P5已经在生产生效，也不代表P6任一测试或恢复演练通过。最终静态检查为59份Markdown、49份核心文档、1722个链接、372条有向/186组双向核心关系、24/24旧文档映射和11/11检查器自检，错误为0；`source_files_checked=0`。首次运行时本次报告文件尚未创建，两处报告链接及受其影响的自检暂时失败；文件生成后按同一命令复跑通过，最终报告覆盖为有效结果，未将首次时序失败冒充产品缺陷。报告见[validation-report-20260903-bm25-p5-p6-plan.json](../工具/validation-report-20260903-bm25-p5-p6-plan.json)。

## 变更记录

| 日期 | 内容 | 原因 |
|---|---|---|
| 2026-09-03 | 同步BM25 P5条件采用和P6-A/B/C生产实施协议的静态文档验收 | 用户要求先确认优化内容并更新文档，确认前不实施项目或生产变更 |
| 2026-09-03 | 同步BM25持久化P4同期对照、初始失败修正、资源结果和P5停止点 | 记录真实收益与噪声边界，不提前作采用或生产切换决定 |
| 2026-09-03 | 同步BM25持久化P3业务接入、同Dense回放、Trace/状态和P4停止点 | 记录正确性证据，不把P3回放误写为性能改善或生产切换 |
| 2026-09-03 | 同步BM25持久化P2实现、双构建、oracle、专项/全量回归及P3停止点 | 用户确认P2后记录实际证据，不把隔离基础设施写成业务性能提升 |
| 2026-09-03 | 同步BM25持久化P1真实等价/mmap结果、优化前系统基线及后续停止点 | 用户确认P1后记录实际证据，不把隔离探针写成业务优化完成 |
| 2026-09-03 | 同步取消1万篇扩展并补充1000篇/120题优化前后完整指标口径 | 记录用户确认的范围变化，避免后续误跑额外数据任务 |
| 2026-09-03 | 新增BM25持久化方案及其需求、设计、开发、测试、运维和导航同步的静态验收 | 按先文档确认、后实施的门禁记录本轮边界 |
| 2026-08-31 | 新增V3-5 F实现、GPU/失败run、否决结果和阶段边界的独立静态文档验收 | 按new_docs流程同步F真实失败结论且不覆盖A—E/B2报告 |
| 2026-08-31 | 新增V3-5 E离线门控实现、结果和阶段边界的独立静态文档验收 | 按new_docs流程同步E真实证据且不覆盖A/B/C/D/B2报告 |
| 2026-08-31 | 新增V3-5补充B2实现、结果和阶段边界的独立静态文档验收 | 按new_docs流程同步B2真实证据且不覆盖A/B/C/D报告 |
| 2026-08-30 | 新增V3-5 D实现、结果和阶段边界的独立静态文档验收 | 按new_docs流程同步D真实证据且不覆盖A/B/C报告 |
| 2026-08-29 | 新增V3-5 C实现、结果和阶段边界的独立静态文档验收 | 按new_docs流程同步C真实证据且不覆盖A/B报告 |
| 2026-08-29 | 新增V3-5 B实现、结果和阶段边界的独立静态文档验收 | 按new_docs流程同步B真实证据且不覆盖A报告 |
| 2026-08-29 | 将Dense差异诊断同步纳入V3-5 A静态文档验收，更新实际链接数 | 保持诊断证据、实验边界与导航一致 |
| 2026-08-29 | 追加V3-5 A实现/验收后的静态文档检查 | 按new_docs流程同步真实实现和测试边界 |
| 2026-08-28 | 追加V3-5方案维护的独立静态验收，区分历史保护快照与日常检查 | 保留首次整理证据，不将方案或旧基线写成本轮产品验证 |
| 2026-08-27 | 新增文档校验实现与验收追踪 | 建立独立new_docs对照体系，保留原文件 |
