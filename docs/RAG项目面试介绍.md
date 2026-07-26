# 中文企业知识库 RAG：面试项目介绍

> 文档定位：从面试官视角介绍项目目标、架构、技术选择、能力边界、工程亮点、代表性问题与验证证据。  
> 更新日期：2026-07-26。  
> 当前状态：核心代码与本地工程链路已完成，全量自动化测试为 `344 passed, 3 skipped`；Dataset v3 仍是预检数据集，真实人工签核和当前生产配置下的完整外部 LLM 验收尚未完成。

## 一句话介绍

这是一个面向中文企业文档的本地 RAG 知识库系统。它不仅能完成多格式文档上传、结构化解析、检索和多轮问答，还重点解决了普通 RAG 项目中经常被忽略的三个问题：

1. 回答中的引用是否真的能落到原始文档；
2. 检索效果是否有固定数据集和指标证明；
3. 出现错误时，能否定位是解析、召回、排序还是生成阶段的问题。

项目当前支持 PDF、DOCX、TXT、Markdown 和 PPTX，使用统一结构模型贯通解析、切块、检索、引用和原文高亮；使用隔离评测集、单变量实验和检索 Trace 保证优化过程可解释、可复现。

## 面试开场：2 分钟版本

我做的是一个中文企业知识库 RAG 系统，技术栈是 Streamlit、FastAPI、SQLite、Chroma、BGE 中文 Embedding、BGE Reranker 和 DeepSeek。

这个项目早期已经能完成“上传文档并问答”，但我认为仅仅能返回一段答案并不能证明系统可靠，所以后续优化主要围绕三点展开。

第一是可追溯性。我设计了统一的文档结构模型，把不同格式统一成带稳定 ID、来源定位和字符偏移的 `ParsedUnit`。LLM 回答只能引用后端分配的 `【来源N】`，后端再把编号映射回真实 chunk，而不是相信模型自己生成的文件名和页码。用户点击引用后可以看到完整解析文档，系统会自动定位并高亮实际引用区间。

第二是可评估性。我建立了隔离于生产数据的评测流水线，覆盖检索命中率、召回率、MRR、回答关键点、引用精确率、拒答准确率和延迟。所有检索优化都尽量采用单变量实验。例如先替换中文 Embedding，再优化 DOCX 表格解析、递归切块、阈值和 Reranker。对于 Dense + BM25 混合检索，我虽然完成了实现，但消融结果没有优于 Dense + Reranker，所以没有为了“技术栈更丰富”而切到生产配置。

第三是可诊断性。我实现了可选的检索 Trace，可以看到 Dense、BM25、RRF、Reranker 和最终上下文各阶段的候选、排名、分数、过滤原因和耗时。这样回答错误时，可以判断问题发生在解析、召回、排序还是 LLM 生成。

工程层面还实现了版本化数据库、失败入库补偿清理、持久化多轮会话、请求幂等、并发冲突检测和启动恢复。当前 23 份真实语料已经重建为 2,438 个结构化单元和 2,584 个 chunks，全量自动化测试为 344 通过、3 跳过。

我也明确限制了项目边界：目前是本地单用户可信环境，不支持 OCR、图片和图表理解、Excel、权限隔离或分布式部署。因此我不会把它描述成已经可以直接服务大型企业的完整知识平台。

## 项目目标

项目不是追求“接入尽可能多的框架”，而是构建一条可以回答以下问题的 RAG 工程链路：

- 文档经过了什么解析和切块？
- 检索到的内容来自哪里？
- LLM 的引用是否真实存在？
- 一次优化到底改善了哪些题，又回归了哪些题？
- 系统失败后会不会留下半份文件、脏向量或悬空元数据？
- 多轮追问如何使用历史，又如何防止历史回答代替当前文档证据？

## 项目框架

### 总体架构

```text
                         ┌──────────────────────────────┐
PDF / DOCX / TXT / MD / PPTX
                         └──────────────┬───────────────┘
                                        │
                              格式 Parser Adapter
                                        │
                                        ▼
                         ┌──────────────────────────────┐
                         │ ParsedBlock / ParsedUnit     │
                         │ 结构、规范文本、定位、offset │
                         └──────────┬───────────┬───────┘
                                    │           │
                              递归切块          │
                                    │           │
                                    ▼           ▼
                                Chroma       SQLite
                              检索文本与     文档结构、状态、
                              轻量 metadata  会话和消息

用户问题
   │
   ├── 有界会话历史 → 追问改写 → standalone question
   │
   ▼
Dense / BM25 / Hybrid 候选
   │
距离阈值过滤
   │
Cross-encoder Reranker
   │
单文档数量限制 + Top K
   │
带【来源N】的上下文
   │
DeepSeek
   │
可信引用映射 + 会话持久化
   │
Streamlit 回答 / Trace / 完整原文高亮
```

### 模块职责

| 层次 | 主要技术 | 职责 |
|---|---|---|
| 前端 | Streamlit | 文档上传、对话管理、问答、引用预览、Trace 展示 |
| API | FastAPI + Pydantic | 参数校验、路由编排、错误码、OpenAPI 文档 |
| 文档解析 | 自定义 Adapter | 五种格式解析为统一强类型结构 |
| 结构组装 | Assembler | 生成稳定 ID、规范文本、连续 sequence 和字符 offset |
| 切块 | 自定义 token-aware chunker | 按段落、句子、子句和字符回退递归切分 |
| Dense 检索 | BGE + Chroma | 中文语义召回、向量持久化和 metadata 查询 |
| Sparse 检索 | jieba + BM25 | 精确术语召回及混合检索实验能力 |
| 融合与精排 | RRF + BGE Reranker | 排名融合和 Cross-encoder 精排 |
| 元数据 | SQLite | 文档生命周期、结构化单元、会话、消息和请求状态 |
| 生成 | DeepSeek OpenAI-compatible API | 基于当前检索证据生成回答 |
| 评测 | 自定义 CLI + pytest | 隔离入库、指标计算、消融、报告和回归检测 |

### 为什么选择 FastAPI

FastAPI 与本项目的匹配点是：

- Pydantic 可以直接作为 API 和内部结构模型的校验层；
- 自动生成 OpenAPI/Swagger，便于独立验证上传、聊天和引用接口；
- 路由与服务层容易分离，单元测试时可以替换 LLM、向量库或配置；
- 对 I/O 型接口友好，未来可以继续演进流式输出或异步任务。

与其他选择的对比：

| 方案 | 优势 | 对本项目的不足 |
|---|---|---|
| Flask | 轻量、自由度高 | 参数模型、错误结构和 API 文档需要额外搭建 |
| Django | ORM、认证和后台管理完整 | 对当前本地单用户 RAG 偏重，核心检索服务会被较大的 Web 框架结构包围 |
| FastAPI | 类型校验、API 文档、测试体验较好 | 不自带完整用户系统和后台，需要按需实现 |

### 为什么选择 Streamlit

项目重点是验证 RAG 链路，而不是构建复杂消费级前端。Streamlit 能快速提供：

- 文件上传、对话、表格、折叠面板和状态展示；
- Trace 候选表和 Chunk 原文的交互式诊断；
- 较低的前端工程成本，适合本地演示和实验系统。

它的代价也很明确：

- DOM 和弹窗行为不是稳定公共 API，复杂交互需要额外适配；
- 页面重跑模型要求显式管理 `session_state`；
- 不适合直接承担大型多用户产品前端。

项目中真实遇到过弹窗关闭按钮随滚动离开视口、深色模式样式不一致等问题，这也说明 Streamlit 的优势是快速验证，不是无限制的 UI 定制。

### 为什么使用自定义 RAG 流水线，而不是直接采用 LangChain 或 LlamaIndex

本项目保留了清晰的服务模块，没有把核心流程交给通用 RAG 编排框架，主要原因是：

- 引用必须与 `chunk_id`、parser 版本、locator 和字符 offset 保持强一致；
- Trace 需要看到每一个候选阶段，而不是只得到框架封装后的最终结果；
- 评测需要固定输入输出契约，避免框架升级引入隐式行为变化；
- 当前链路规模可控，自定义实现能减少不必要依赖和抽象。

这种选择不是说通用框架没有价值。LangChain/LlamaIndex 在连接器、Agent、工作流编排和原型速度上更有优势；本项目选择的是更高的可控性和可解释性，代价是解析、状态管理和评测工具需要自己实现。

### 为什么选择 Chroma，而不是 FAISS、Milvus 或 Qdrant

当前生产语料只有 2,584 个 chunks，目标是单机本地知识库。Chroma 提供持久化、metadata 和较低运维成本，足够支撑当前规模。

| 方案 | 适合场景 | 本项目判断 |
|---|---|---|
| FAISS | 高性能本地向量搜索 | 本身不解决完整的 metadata、持久化和文档级删除管理 |
| Chroma | 本地开发、小中型知识库、快速持久化 | 当前规模下实现成本和能力最平衡 |
| Milvus/Qdrant | 大规模、多租户、独立向量服务 | 扩展性更强，但会增加部署与运维复杂度 |

项目曾把 Milvus 迁移设为条件项，但前置的混合检索实验没有证明需要迁移，因此没有为了架构“看起来更大”而增加基础设施。

### 为什么选择 SQLite

SQLite 保存文档登记、结构化来源单元、会话、消息和请求状态：

- 比 JSON 文件更适合事务、唯一约束和外键级联；
- 本地部署不需要额外数据库服务；
- 可以用 `BEGIN IMMEDIATE` 实现单会话的并发和幂等控制；
- 通过显式 schema version 拒绝新旧数据库静默混写。

当项目进入多用户、跨进程写入或服务集群阶段，应迁移到 PostgreSQL 等服务型数据库。当前选择是针对单机可信环境的工程折中，不是对所有规模都最优。

### 为什么选择 BGE 中文模型和 Reranker

早期 MiniLM baseline 在中文语料上的 Hit Rate 只有 41.18%。在保持其他变量不变的情况下，切换 `BAAI/bge-large-zh-v1.5` 后，legacy 数据集 Hit Rate 提升到 82.35%，MRR 从 18.73% 提升到 69.61%。

后续加入 `BAAI/bge-reranker-v2-m3`，在 21 题阶段评测中达到：

- Hit Rate：100%；
- Recall：94.12%；
- MRR：97.06%。

代价是 CPU 检索平均耗时增加到约 6.879 秒。因此生产配置采用高质量模式，但代码默认关闭 Reranker，避免缺少本地大模型的新环境无法启动。这体现的是质量、延迟、资源和可部署性之间的显式权衡。

### 为什么保留 DeepSeek 的 OpenAI-compatible 接口

项目通过 OpenAI SDK 和可配置 `base_url/model` 调用 DeepSeek。好处是生成服务与检索、引用和会话逻辑解耦，未来更换兼容接口的模型时，不需要重写整个 RAG 主链路。

这里的可替换性主要存在于 API 契约层；不同模型的提示词遵循度、拒答行为和引用输出仍必须重新评测，不能只替换模型名就假设质量保持不变。

## 项目能力边界

### 已实现并经过代码或本地链路验证

- 上传并解析 PDF、DOCX、TXT、Markdown、PPTX；
- 保留标题、段落、列表、表格、代码块、引用等基础文本结构；
- 区分 PDF 真实页、DOCX/TXT 逻辑页、Markdown 章节和 PPTX 幻灯片；
- 基于 BGE 的中文 Dense 检索；
- BM25、RRF Hybrid 和 Reranker 实验能力；
- 距离阈值过滤、单文档候选数量限制和 Top K 上下文控制；
- 基于 `【来源N】` 的可信引用映射；
- 展示完整解析文档，自动定位并高亮引用区间；
- 可选的多阶段检索 Trace 和耗时统计；
- 持久化多轮对话、追问改写、有界历史和刷新恢复；
- 首次提问自动创建会话，支持重命名、删除和失败重试；
- 请求 ID 幂等、会话尾部冲突检测、pending 超时恢复；
- 文档版本化上传、失败补偿清理和按稳定 ID 删除；
- 隔离生产数据的评测 CLI、消融实验、报告和 run compare；
- Windows 本地一键启动及后端健康检查。

### 代码已实现，但需要区分验证程度

- 多轮对话代码、Mock/API 测试已通过，但当前生产配置下的真实外部 LLM 多轮冒烟此前受网络连接影响，不能描述为已经完成最终外部验收；
- Dataset v3 已完成结构化预检，但仍未通过真实人工签核，不能称为正式冻结测试集；
- v3 的 52 项检索预检使用 gold standalone question 展开对话轮次，不能替代真实追问改写质量评测；
- Dark/Light 主题适配和自动滚动已有组件测试，但最终视觉效果仍应通过真实浏览器人工验收。

### 当前不能实现或不应宣传的能力

- 不支持扫描 PDF OCR；PDF 必须包含可提取文本层；
- 不支持图片文字、图表、流程图、公式和多模态语义理解；
- 不支持 Excel、旧版 `.doc/.xls/.ppt`；
- 不解析 PPTX SmartArt、图表、演讲者备注和图片内容；
- 不保证恢复原始 Word/PDF/PPT 的视觉排版；预览的是完整解析文本及基础结构；
- 不支持网页、网盘、数据库、邮件等自动数据源同步；
- 不支持增量监听目录或定时自动重建索引；
- 不支持用户认证、RBAC、租户隔离和文档级权限；
- 当前是本地单用户可信环境，不能直接称为企业级多租户系统；
- 不支持分布式向量检索、水平扩容和高可用；
- 不提供完整的生产监控平台；Trace 是受控调试功能，不是 OpenTelemetry/APM；
- 前端当前不是 token 级流式回答；
- 不提供 Agent 工具调用、自动任务执行或互联网搜索；
- 无法从根本上保证 LLM 零幻觉，只能通过证据约束、可信引用、拒答和评测降低风险；
- 单文件限制 50 MB；完整解析文档预览也设置了字符上限；
- 本地 BGE 和 Reranker 需要预先缓存模型，并受到 CPU/GPU、内存和冷启动时间限制。

## 项目亮点

### 亮点一：引用不是“展示一个文档名”，而是一条可验证的证据链

#### 解决的问题

常见 RAG Demo 会直接让 LLM 输出“来源：某某文件第几页”，或者在回答后无条件展示检索 Top K。这两种方式都可能产生伪引用：

- 模型可能编造文件名或页码；
- 回答没有使用某个 chunk，但系统仍把它展示为引用；
- 文档重新切块后，旧页码或旧 chunk 无法稳定定位。

#### 实现方式

1. 检索上下文由后端编号为 `【来源1】`、`【来源2】`；
2. Prompt 限制 LLM 只能输出真实存在的来源编号；
3. 回答结束后，后端正则提取来源编号；
4. 编号只能映射到本轮真实检索结果，越界编号直接丢弃；
5. 文档名、来源位置、chunk ID 和 excerpt 全部由后端 metadata 生成；
6. 没有引用标记时返回空引用，不再使用 Top K fallback 伪装成引用。

#### 原文高亮如何实现

每个 chunk 都带有：

- `document_id`；
- `parser_version`；
- `unit_id`；
- `location_label`；
- `start_char/end_char`；
- 稳定 `chunk_id`。

用户点击引用时，预览服务会重新校验：

- 文档是否仍处于 ready 状态；
- chunk 是否属于该文档；
- parser 版本是否一致；
- locator metadata 是否与 SQLite 中的结构化 unit 一致；
- `chunk_id` 是否符合稳定 offset 规则；
- `canonical_text[start:end]` 是否与 Chroma 中的 chunk 内容完全一致。

只有所有检查通过，前端才展示完整解析文档，并在目标 unit 中按字符 offset 高亮引用内容。所有文本先做 HTML 转义，避免文档内容注入前端。

#### 面试价值

这体现的不是一个 UI 功能，而是跨越 parser、chunker、向量 metadata、SQLite、API 和前端 renderer 的端到端数据一致性设计。

### 亮点二：评测集让“优化”从主观体验变成可回归实验

#### 解决的问题

只手动问几个问题，很容易因为样本选择偏差得出错误结论。一个参数可能改善某题，却让其他题回归；检索命中也不等于回答正确。

#### 实现方式

评测系统与生产目录隔离，独立创建 Chroma、SQLite 和上传目录，避免测试污染生产数据。数据集记录：

- 问题、题型和是否可回答；
- 标准答案和关键点组；
- gold 文档和唯一证据片段；
- v3 中的 parser 版本、unit ID、locator、offset 和 snippet hash；
- 不可回答原因；
- 人工审核状态。

指标分为四类：

| 维度 | 指标 |
|---|---|
| 检索 | Hit Rate、Recall、MRR |
| 回答 | Keypoint Coverage、Strict Accuracy |
| 引用 | Citation Precision、Citation Recall、Emission Rate、Grounded |
| 拒答 | Refusal Accuracy、False Refusal Rate、不可回答题伪引用 |

报告不仅给汇总数字，还生成：

- 每题原始回答和完整检索结果；
- 失败案例：gold 证据、实际检索、实际引用和回答节选；
- 人工 review 文档；
- 两个 run 的指标差值、改善题和回归题清单。

#### 数据证据

- legacy baseline 的 Hit Rate 为 41.18%，MRR 为 18.73%；
- 经过中文 Embedding、表格解析、递归切块、阈值和 Reranker 的逐阶段实验，21 题阶段 Hit Rate 达到 100%，MRR 达到 97.06%；
- Dataset v3 预检包含 35 道单题和 6 组对话的 17 个 turn，共 52 项；
- v3 预检 Hit Rate/Recall 为 97.67%，MRR 为 95.35%，保留了唯一漏检题而没有修改 gold 掩盖问题。

不同版本数据集的数字不能直接横向比较，因此文档分别陈述，不把 v3 结果包装成相对 legacy baseline 的同口径提升。

#### 面试价值

体现了实验设计、数据治理、指标定义、回归意识和结果诚实性，而不仅是模型调用能力。

### 亮点三：检索 Trace 能定位错误发生在哪一层

#### 解决的问题

用户看到错误回答时，可能有四种完全不同的原因：

1. parser 没解析到；
2. Dense/BM25 没召回；
3. Reranker 排错或阈值过滤掉；
4. 证据正确，但 LLM 没有按证据回答。

如果只保留最终 Top K，这些问题无法区分。

#### 实现方式

TraceCollector 按实际执行顺序记录：

- Dense 候选、distance 和阈值过滤原因；
- BM25 候选、score 和 rank；
- RRF 融合排名；
- Reranker score 和新排名；
- 最终入选结果；
- query embedding、Dense、Sparse、RRF、Reranker、上下文组装、LLM 和总耗时；
- 最终上下文的 chunk 数、字符数和 token 数。

完整 Chunk 按 `chunk_id` 去重保存，前端通过阶段表格查看，不复制多份原文。

Trace 默认关闭，因为开启后会把未入选候选原文返回前端，可能暴露知识库内容。这个开关本身也是安全边界的一部分。

#### 面试价值

Trace 把 RAG 从黑盒调用变成可调试流水线，也能支持后续性能优化和线上故障分析。

### 亮点四：统一结构模型解决多格式解析的长期一致性

五种格式并不共享“页”这个概念：

- PDF 有真实页；
- DOCX/TXT 只有系统划分的逻辑页；
- Markdown 更适合按章节；
- PPTX 按幻灯片；
- 未来 Excel 应按 sheet 和 cell range。

项目使用 `SourceLocator` 明确区分这些位置类型，避免前端把所有数字都错误称为页码。

所有 Adapter 只负责忠实提取 `BlockDraft`，公共 Assembler 统一生成：

- 稳定 `unit_id/block_id`；
- 连续 sequence；
- `canonical_text`；
- block 的 `start_char/end_char`；
- 强类型 Pydantic 校验。

chunker 只依赖 `ParsedUnit.canonical_text`，不理解 PDF、Word 或 PPTX。这样新增格式不会继续复制切块和 offset 逻辑。

### 亮点五：多轮对话没有让历史污染事实证据

多轮 RAG 的核心问题不是保存聊天记录，而是追问可能包含指代：

```text
第一问：BGE 和 MiniLM 的差异是什么？
第二问：那它为什么更适合中文？
```

直接检索“那它为什么更适合中文”会丢失实体；直接把全部历史拼进检索又会引入噪声。

项目的处理流程是：

```text
原始追问 + 有界历史
  → 改写成独立问题
  → 用独立问题检索当前文档
  → 用原始问题 + 有界历史保持表达连贯
  → 事实仍只允许来自本轮检索证据
```

历史窗口同时受消息条数、token 数和单条字符数限制。改写失败时回退原问题，不让改写服务故障直接中断整条问答链路。

持久化层还实现了：

- `request_id` 幂等重放；
- `expected_last_message_id` 乐观并发校验；
- 同会话 pending 请求冲突；
- 失败请求显式重试；
- 服务重启后的超时 pending 恢复；
- citation snapshot，保证历史回答的引用摘要不会随重新检索变化。

### 亮点六：文档入库具有生命周期和补偿清理

上传不是简单地“保存文件再写向量”，而是：

```text
临时文件
  → SQLite processing
  → parse
  → structured units
  → chunk
  → SQLite document_units
  → Chroma
  → 原子移动正式文件
  → SQLite ready
```

任一阶段失败都会尝试删除：

- 已写入的向量；
- 已写入的 document units；
- 临时文件和可能的正式文件；
- 并把文档状态记录为 failed，保留错误供排查。

同名上传会产生独立 `document_id`，因此支持版本化，不依赖文件名作为内部主键。

## 开发中遇到的代表性问题

### 问题一：中文语义检索 baseline 很弱

#### 如何发现

早期系统使用 `all-MiniLM-L6-v2`。手工体验有时能回答，但固定 21 题评测显示：

- Hit Rate：41.18%；
- Recall：32.35%；
- MRR：18.73%；
- 可回答问题误拒答率：41.18%。

问题不是“LLM 不够强”，而是很多 gold 证据根本没有进入上下文。

#### 解决方案

按单变量顺序做实验：

1. 只替换为 `bge-large-zh-v1.5`，比较 query instruction 开关；
2. 保持模型不变，增加 DOCX 表格原序解析；
3. 比较固定切块和 400/80、600/100、800/120 递归切块；
4. 离线扫描 distance threshold；
5. 比较 Reranker 候选数 15/20/30；
6. 再比较 Dense、BM25、Hybrid 及是否加 Reranker。

#### 结果与反思

最终生产选择 Dense + Reranker。Hybrid + Reranker 虽然已经实现，但没有带来指标增量，还会让更多不可回答题进入 LLM，因此没有部署。

这个问题的关键收获是：RAG 优化不能只靠换模型，也不能假设更复杂的检索组合一定更好；必须固定数据和其他变量。

### 问题二：多格式 parser 的隐式字典导致来源定位和 offset 不可靠

#### 如何发现

旧 parser 使用松散 `dict`，代码默认所有来源都有 page number。加入 Markdown 和 PPTX 后出现多个冲突：

- Markdown 章节不是页；
- PPTX 需要 slide number；
- DOCX 表格、标题和段落必须保持原序；
- 前端高亮要求 chunk offset 与完整来源文本严格一致；
- 每种 parser 独立拼接文本会产生不同空白和 offset 规则。

#### 解决方案

- 建立强类型 `ParsedBlock`、`ParsedUnit`、`SourceLocator`；
- 使用公共 Assembler 生成唯一规范文本和 offset；
- 数据库存储 parser version、规范文本和 structure JSON；
- 读取 structure JSON 时重新经过 Pydantic 校验；
- Chroma 只保存检索需要的文本和轻量定位 metadata；
- schema 不兼容时拒绝启动，不静默双读旧数据；
- 完成一次受控的生产空库重建。

#### 结果与反思

统一模型增加了前期改造成本，但消除了格式分支向 chunker、citation 和 renderer 扩散的问题。新增格式时只需要实现 Adapter，不需要重写后续链路。

### 问题三：引用看起来正确，但可能是模型编造或系统伪配

#### 如何发现

早期引用协议允许模型自由输出来源信息，并存在“回答没有引用时展示 Top K”的 fallback。这样 UI 看起来总有来源，但无法证明回答真正使用了它。

#### 解决方案

- 改为本轮检索结果的 1-based source ID；
- 后端验证 ID 范围并映射真实 metadata；
- 删除 Top K citation fallback；
- 对引用预览做 parser version、locator、chunk ID、offset 和内容切片强一致性检查；
- 评测从原始回答解析 `【来源N】`，不存在的编号计为无效引用。

#### 结果与反思

系统宁可显示“无引用”，也不展示无法证明的来源。这个取舍降低了表面完成度，但提高了可信度。

### 问题四：只展示命中 Chunk 不等于用户理解的“查看原文”

#### 如何发现

最初引用预览只返回目标页或 unit，用户会认为文档其他内容丢失；页面下方又单独展示一次完整 Chunk，与上方高亮内容重复。

#### 解决方案

- SQLite 保存整份文档的结构化 units；
- 预览 API 返回全部 units，并单独标记 `target_unit_id`；
- 前端按原始 unit 顺序渲染整篇解析文档；
- 只在目标 unit 的 offset 区间高亮；
- 删除重复的 Chunk 原文区域；
- 打开预览后自动滚动到首个高亮；
- 关闭按钮固定在可见位置并适配明暗主题。

#### 结果与反思

“完整原文”在本项目中明确指完整解析文本和基础语义结构，不等于原始二进制文档的像素级还原。功能命名必须与真实能力一致。

### 问题五：多轮对话中的追问无法直接用于检索

#### 如何发现

第二轮问题常含“它、这个方案、那为什么”等指代。直接向量化会召回错误内容；把全部历史直接拼到 query 又会稀释本轮意图，并可能把历史助手的错误回答当作事实。

#### 解决方案

- 保存原始问题；
- 使用有界历史把追问改写成 standalone question；
- 只用 standalone question 检索；
- 回答生成可读取有限历史保持连贯；
- system prompt 明确历史不是事实证据；
- 改写失败回退原问题；
- 使用消息序号、请求状态和最后消息 ID 处理幂等与并发。

#### 结果与反思

多轮 RAG 需要把“对话连贯性”和“事实检索依据”分离。只保存聊天记录并不等于实现了可靠多轮问答。

### 问题六：生产切换可能混入旧 schema 和旧向量

#### 如何发现

parser、Embedding 维度、chunk ID 和 metadata 契约都发生过变化。直接复用旧数据库会产生：

- 384 维旧向量与 1,024 维 BGE 向量不兼容；
- 旧 chunk 缺少 unit 和 locator metadata；
- 新预览无法验证旧 offset；
- 同一数据库中出现新旧 schema 静默混写。

#### 解决方案

- 引入显式 `schema_meta` 和 parser version；
- 只有空数据库才自动 bootstrap；
- 遇到未版本化或不完整 schema 时拒绝启动；
- 重建脚本使用独立目标目录，不覆盖旧索引；
- 完成数量、document ID 和向量维度校验后才同步登记；
- 最终在确认范围后执行受控清理和全量生产重建。

#### 结果与反思

对无法可靠自动迁移的数据，显式拒绝和重建比“尽量兼容”更安全。兼容层不是越多越好。

### 问题七：本地模型和外部 API 带来环境不确定性

#### 如何发现

- Hugging Face 网络不稳定会导致服务启动时隐式下载或检查超时；
- Reranker 模型约 2.14 GiB，首次加载较慢；
- DeepSeek 网络波动会让真实端到端测试失败，但不代表本地检索代码错误。

#### 解决方案

- Embedding 和 Reranker 只从本地缓存加载；
- CI 运行不依赖本地权重的测试子集；
- external 测试默认跳过，必须显式授权；
- 检索评测与 LLM 评测分离；
- Trace 分别记录模型加载后各阶段耗时；
- 对外部网络未通过的部分明确标记为待验收，不用 Mock 结果冒充真实 API 通过。

#### 结果与反思

工程测试需要区分代码正确性、模型资源可用性和外部服务可达性。三者失败时的结论不能混在一起。

## 测试与验证证据

### 自动化测试

当前完整测试结果：

```text
344 passed, 3 skipped
```

覆盖范围包括：

- 五种格式 Adapter 和解析顺序；
- 统一数据模型、稳定 ID、sequence 和 offset 不变量；
- token-aware chunker；
- Chroma 和 SQLite 存储；
- schema bootstrap、版本门禁、事务和外键级联；
- Dense、BM25、RRF、Reranker 和阈值；
- citation source ID、伪引用拒绝和完整原文预览；
- 前端 HTML 安全转义、高亮、对话和 Trace；
- 会话 CRUD、幂等、并发、失败重试和启动恢复；
- Dataset v3 schema、manifest、review gate 和评测 CLI；
- API 上传、问答和引用集成。

3 个跳过项都是默认不在普通测试中运行的真实外部 LLM API 测试。它们需要显式参数，避免常规回归测试产生费用或受到网络波动影响。

### 真实数据链路

当前生产目录已完成：

- 23 份文档真实上传；
- 23/23 文档 ready；
- 2,438 个 `document_units`；
- 2,584 个 Chroma chunks；
- 结构化检索、引用映射和来源预览冒烟。

### Dataset v3 预检

- 35 道单题；
- 6 组多轮对话，共 17 个 turns；
- 52 个 answer items；
- 43 个可回答，9 个不可回答；
- 80 条 evidence；
- Hit Rate/Recall：97.67%；
- MRR：95.35%；
- 1 道已知漏检。

这些结果只能称为 structured preflight，因为：

- review 仍是 draft；
- 未完成完整 52 项真实 LLM 回答评测；
- dialogue turn 的下游检索使用 gold standalone question；
- 真实人工签核尚未完成。

## 工程取舍

### 没有部署 Hybrid 检索

Hybrid + Reranker 在 v2 消融中没有超过 Dense + Reranker，且增加了域外题进入 LLM 的比例。项目保留代码能力，但生产继续使用 Dense + Reranker。

这个决定说明“实现一个能力”和“证明它值得上线”是两件事。

### 没有迁移 Milvus

当前只有 2,584 个 chunks，Chroma 足够。迁移 Milvus 会增加部署复杂度，却没有当前规模下的收益证据，因此把迁移保留为数据量和并发增长后的条件项。

### 没有实现 OCR

OCR 会引入额外模型、语言识别、版面分析、表格恢复、质量评估和算力成本。项目当前目标是先把可提取文本的结构、引用和评测做可靠，因此没有把 OCR 当成一个简单开关加入主链路。

### 选择重建而不是长期兼容旧数据

旧 parser、旧 Embedding 维度和旧 metadata 无法满足新引用强一致性。项目选择版本门禁和一次性重建，避免长期维护双读逻辑和无法证明正确的自动迁移。

## 如果继续迭代

优先级建议：

1. 完成 Dataset v3 人工签核和完整外部 LLM 评测；
2. 对真实多轮追问改写做独立指标和错误集；
3. 处理唯一漏检题，但必须在全量数据上验证不能只修单题；
4. 增加浏览器级 E2E 和 Dark/Light 视觉回归；
5. 增加认证、用户与文档权限模型；
6. 将长耗时解析和 Embedding 改为后台任务，提供进度和取消；
7. 增加结构化日志、指标、请求链路 ID 和 OpenTelemetry；
8. 数据量与并发达到门槛后，再评估 PostgreSQL、Redis 和独立向量数据库；
9. 把 OCR 作为独立 pipeline，建立扫描件、表格和版面数据集后再决定是否合入；
10. 增加流式回答，但需要同时设计失败状态、引用何时落盘和断线恢复。

## 面试官常见追问

### 1. 这个项目和普通 RAG Demo 最大区别是什么？

普通 Demo 重点是“能回答”；这个项目重点是“回答依据可验证、优化结果可量化、失败阶段可定位”。具体落在可信引用、统一结构 offset、隔离评测和检索 Trace 四个机制上。

### 2. 如何降低幻觉？

不能保证零幻觉，但做了多层约束：

- distance threshold 过滤低相关候选；
- 只把 Top K 当前证据交给 LLM；
- prompt 要求信息不足时拒答；
- 历史不能作为事实来源；
- 回答只能使用真实 source ID；
- 后端拒绝越界或不完整引用；
- 数据集同时评估回答关键点、拒答和伪引用。

### 3. 为什么不用更大的 LLM 直接解决？

如果 gold 证据没有召回，再大的 LLM 也只能猜；而企业知识库要求回答来自私有文档。项目先通过检索指标定位召回问题，再单独评估生成，避免把所有错误都归因于模型大小。

### 4. 如何证明一次优化有效？

固定语料、数据集和其余配置，只改变一个主要变量，生成独立 label 的 report；对比 Hit/Recall/MRR、回答、引用、拒答和延迟，并检查“旧版本通过、新版本失败”的回归题清单。

### 5. 为什么 source ID 比让模型输出文件名更可靠？

source ID 是本轮上下文的有限索引。后端只接受范围内编号，并从真实检索结果填充文档名、locator 和 chunk ID。模型不能通过自由文本创建一个系统认可的新来源。

### 6. 为什么 Chroma 和 SQLite 要同时存在？

Chroma负责相似度检索，只保存检索文本和轻量 metadata；SQLite负责事务状态、完整结构、会话和强约束。把完整结构复制到向量 metadata 会增加冗余和一致性风险，把向量搜索交给 SQLite 又不适合。

### 7. 多轮对话为什么要改写问题？

追问包含指代，原始文本不适合独立检索。改写用于补全实体，历史只帮助理解意图；检索和事实回答仍基于本轮文档证据，从而避免历史回答变成新的知识来源。

### 8. 系统现在能否直接上线企业生产？

不能。当前是本地单用户可信环境，缺少认证、授权、租户隔离、后台任务、完整监控、高可用和容量测试。更准确的定位是：具备较完整工程验证能力的本地 RAG 原型和实验平台。

### 9. 项目里最能体现工程能力的部分是什么？

不是单个模型，而是跨层不变量：同一条引用从 parser 的规范文本、chunk offset、Chroma metadata、SQLite unit、API 校验到前端高亮都能被验证；同时每次算法调整都有隔离评测和回归报告。

## 简历表述参考

可以压缩为以下三条：

- 设计并实现中文企业文档 RAG 系统，基于 FastAPI、Streamlit、Chroma、SQLite、BGE Embedding/Reranker 和 DeepSeek，支持 PDF、DOCX、TXT、Markdown、PPTX 的统一结构化解析、持久化多轮问答和完整来源预览。
- 建立稳定 source ID、parser version、locator 与字符 offset 贯通的可信引用链路，实现引用真实性校验、完整文档自动定位高亮和多阶段检索 Trace。
- 构建生产隔离的 RAG 评测与消融流水线，覆盖检索、回答、引用、拒答和延迟指标；通过单变量实验完成中文 Embedding、表格解析、递归切块、阈值和 Reranker 优化，并基于实验结果拒绝无增益的 Hybrid/Milvus 迁移。

## 代码导览

| 主题 | 主要文件 |
|---|---|
| API 入口与配置 | `backend/main.py`、`backend/config.py` |
| 统一结构模型 | `backend/models/document_structure.py` |
| 五格式 Adapter | `backend/services/parsers/` |
| 切块 | `backend/services/chunker.py` |
| 文档入库 | `backend/routers/upload.py` |
| Dense/BM25/Hybrid/Reranker | `backend/services/retriever.py`、`sparse_retriever.py`、`fusion.py`、`reranker.py` |
| 引用 | `backend/utils/citation.py` |
| 完整原文预览 | `backend/services/structured_citation_preview.py`、`frontend/citation_view.py` |
| 检索 Trace | `backend/services/retrieval_trace.py`、`frontend/trace_view.py` |
| 多轮会话 | `backend/services/conversation_store.py`、`conversation_context.py`、`backend/routers/chat.py` |
| SQLite schema | `backend/services/metadata_schema.py`、`unit_store.py` |
| 评测 | `eval/eval.py`、`eval/metrics.py`、`eval/report.py`、`eval/dataset_v3.py` |
| 自动化测试 | `tests/` |

## 最后总结

这个项目的核心价值不是“组合了哪些热门组件”，而是围绕 RAG 的可靠性建立了一套工程闭环：

```text
强类型结构解析
  → 可定位的切块
  → 可比较的检索实验
  → 受证据约束的生成
  → 可验证的引用
  → 可诊断的 Trace
  → 可复现的评测与回归
```

面试介绍时应把重点放在为什么做这些设计、如何用数据选择方案、遇到失败如何定位，以及哪些能力仍然没有完成。明确边界不会削弱项目，反而能证明具备工程判断和生产意识。
