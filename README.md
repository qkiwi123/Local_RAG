# 中文企业知识库 RAG

一个以“可评估、可追溯、可复现”为核心的中文 RAG 项目。系统支持上传 PDF、DOCX、TXT，完成解析、切分、向量检索和 DeepSeek 问答，并通过稳定的 `【来源N】` 协议返回可落到真实 chunk 的引用。

当前工作重点不是堆叠功能，而是用固定黄金集、单变量实验和回归报告逐步提高中文检索质量。

## 当前架构

```text
Streamlit
   │ HTTP
   ▼
FastAPI
   ├── upload: file → parser → chunker → embedding → Chroma
   │                         └── SQLite document registry
   └── chat: query → retrieve → context → DeepSeek → source ID citations

eval
   └── isolated corpus/index → 21 questions → metrics/report/compare
```

核心目录：

```text
backend/                 FastAPI、数据模型和 RAG 服务
frontend/app.py          Streamlit 界面
eval/                    黄金集、隔离评估 CLI、报告生成
tests/                   unit/integration/external 测试
docs/交接_阶段一优化.md   当前阶段交接和施工约束
```

## 已有能力

- PDF、DOCX、TXT 解析；
- Token-aware 中文递归切分和 Chroma 余弦检索；
- DeepSeek OpenAI-compatible API；
- 基于 source ID 的可信引用，不使用 Top-K 伪引用 fallback；
- 稳定 `document_id`、版本化同名上传、SQLite 文档登记；
- 失败入库清理和按 document ID 删除；
- 21 题中文评估集、分类指标、失败案例和 run compare；
- 测试数据与生产 Chroma/SQLite/上传目录隔离；
- 真实外部 API 测试默认跳过。

## Baseline

当前 baseline 使用 `all-MiniLM-L6-v2`、500/50 字符滑窗、Top 5 检索。

| 指标 | 值 |
|---|---:|
| Hit Rate | 41.2% |
| Recall | 32.4% |
| MRR | 18.7% |
| Keypoint Coverage | 57.7% |
| Citation Emission Rate（旧引用协议） | 11.8% |
| Refusal Accuracy | 100% |
| False Refusal Rate | 41.2% |
| 平均总延迟 | 约 2.03s |

完整口径见 `eval/README.md` 和 `eval/runs/baseline/report.md`。baseline 的引用数据使用历史旧协议；新 run 使用 `【来源N】` 可信引用协议。

## P1-1 中文 Embedding 实验

保持 500/50 字符滑窗、Top 5、阈值和 parser 不变，对
`BAAI/bge-large-zh-v1.5` 做 query instruction A/B。最终选择 **noinst**：

| 指标 | baseline | BGE large noinst | 变化 |
|---|---:|---:|---:|
| Hit Rate | 41.18% | 82.35% | +41.17pp |
| Recall | 32.35% | 76.47% | +44.12pp |
| MRR | 18.73% | 69.61% | +50.88pp |
| Strict Accuracy | 41.18% | 82.35% | +41.17pp |
| False Refusal Rate | 41.18% | 11.76% | -29.42pp |
| Refusal Accuracy | 100% | 100% | 持平 |

noinst 与 inst 的 Hit Rate、Recall 相同；noinst 的 MRR 高 2.94pp，检索平均延迟
为 0.067s（inst 为 0.089s），且没有 baseline 通过而新方案失败的回归题。
默认配置因此切换为 BGE large，query instruction 留空。

完整实验数据已固化在 `eval/results/p1-1-bge/`：两组 `report.md`、
`report.json` 及 baseline/noinst/inst 对比报告均受 Git 跟踪。

## P1-2 文本清洗与 DOCX 表格

保持 BGE noinst、500/50、Top 5 和阈值不变，只增加保守空白清洗、DOCX 段落/表格
原序解析、表格 Markdown 化和 `content_type` 元数据：

| 指标 | P1-1 BGE noinst | P1-2 DOCX table | 变化 |
|---|---:|---:|---:|
| Hit Rate | 82.35% | 94.12% | +11.77pp |
| Recall | 76.47% | 85.29% | +8.82pp |
| MRR | 69.61% | 75.49% | +5.88pp |
| Strict Accuracy | 82.35% | 82.35% | 持平 |
| Citation Precision | 94.44% | 100.00% | +5.56pp |
| False Refusal Rate | 11.76% | 0.00% | -11.76pp |
| Refusal Accuracy | 100.00% | 100.00% | 持平 |

表格金丝雀 q11/q12 分别以第 2/第 1 名命中，且没有普通题从检索命中退化为未命中。
完整报告和 P1-1 对比保存在 `eval/results/p1-2-docx-table/`。

## P1-3 Token-aware 递归切分

保持 P1-2 parser、BGE noinst、Top 5 和阈值不变，比较 recursive 400/80、600/100、
800/120，使用 BGE tokenizer 将单 chunk 硬上限设为 480 tokens。最终选择 400/80：

| 指标 | P1-2 fixed 500/50 | P1-3 recursive 400/80 | 变化 |
|---|---:|---:|---:|
| Hit Rate | 94.12% | 94.12% | 持平 |
| Recall | 85.29% | 88.24% | +2.95pp |
| MRR | 75.49% | 81.37% | +5.88pp |
| Keypoint Coverage | 90.00% | 94.12% | +4.12pp |
| Strict Accuracy | 82.35% | 94.12% | +11.77pp |
| Citation Precision | 100.00% | 97.92% | -2.08pp |
| False Refusal Rate | 0.00% | 5.88% | +5.88pp |
| Refusal Accuracy | 100.00% | 100.00% | 持平 |

新切分器按段落、句子、子句、字符兜底的层级选择边界，保留 overlap，并记录稳定
chunk ID、逻辑页 `start_char/end_char`、字符数、token 数和 `content_type`。正式评估恢复
q02，并改善 q08、q16；已知回归是 q05 的黄金证据被同文档更高相似块挤出最终 Top 5。
完整离线候选、正式报告和 P1-2 对比保存在
`eval/results/p1-3-recursive-chunking/`。

## P1-4 检索阈值与拒答校准

保持 P1-3 的语料、parser、切分、Embedding 和 Top 5 不变，使用真实评测检索结果扫描
`0.50–0.75` 及历史值 `1.50`。最终选择 Chroma cosine distance `0.67`：

| 指标 | P1-3 threshold 1.50 | P1-4 threshold 0.67 | 变化 |
|---|---:|---:|---:|
| Hit Rate | 94.12% | 94.12% | 持平 |
| Recall | 88.24% | 88.24% | 持平 |
| MRR | 81.37% | 81.37% | 持平 |
| 阈值诱发误拒答率 | 0.00% | 0.00% | 持平 |
| 不可回答题进入 LLM 比例 | 100.00% | 75.00% | -25.00pp |
| Refusal Accuracy | 100.00% | 100.00% | 持平 |

`0.66–0.68` 是当前数据上的等价最优平台，选择中点可避免贴近边界。q18 被检索层短路，
不再调用 LLM；q19–q21 与语料存在语义重叠，若继续降低阈值会先误伤可回答的 q03，
因此保留给 LLM 做上下文不足判断。完整扫描、真实 21 题评测和 P1-3 对比保存在
`eval/results/p1-4-threshold-calibration/`。

## P1-5 Cross-encoder Reranker

在 `distance <= 0.67` 后保留更多 Dense 候选，再使用 `BAAI/bge-reranker-v2-m3`
按 query/chunk 相关性精排，最后执行每文档最多 3 条和 Top 5 截断。离线扫描
候选数 15/20/30 的质量完全相同，因此选择延迟最低的 15：

| 候选数 | Hit Rate | Recall | MRR | 检索 P95 |
|---:|---:|---:|---:|---:|
| **15** | **100.00%** | **94.12%** | **97.06%** | **8.628s** |
| 20 | 100.00% | 94.12% | 97.06% | 9.596s |
| 30 | 100.00% | 94.12% | 97.06% | 12.831s |

真实 DeepSeek 21 题对比 P1-4：Hit Rate `+5.88pp`、Recall `+5.88pp`、MRR
`+15.69pp`、Strict Accuracy `+5.88pp`，且无回归题；P1-3/P1-4 的已知失败题 q05
被修复。代价是 CPU 检索平均耗时由轻量 Dense 检索增加到 `6.879s`，总耗时由
`2.043s` 增至 `9.314s`。生产 `.env` 已启用该高质量模式；代码层仍默认关闭，便于
缺少本地模型的 CI/新环境安全启动。完整候选扫描、正式报告、逐题数据、P1-4 对比和
生产 HTTP 冒烟结果保存在 `eval/results/p1-5-reranker/`。

## P2-1 Dense + BM25 + RRF 混合检索

应用侧已增加中文 BM25（jieba 分词 + rank-bm25）、基于稳定 `chunk_id` 的 RRF 融合，
并支持 `dense`、`bm25`、`hybrid` 三种召回模式。RRF 只融合排名，不直接混加 Dense
distance、BM25 score 与 Reranker score；索引内容变更后 BM25 快照按 revision 懒重建。

dataset v2 在原 21 题外增加 6 道精确术语题。五组真实离线消融复用同一次 68 chunks
入库，结果如下：

| 模式 | Hit Rate | Recall | MRR | Lexical Recall | 不可回答题进入 LLM | P95 |
|---|---:|---:|---:|---:|---:|---:|
| Dense | 90.91% | 86.36% | 78.79% | 80.00% | 80.00% | 0.088s |
| BM25 | 95.45% | 90.91% | 86.74% | 100.00% | 100.00% | 0.000s |
| Hybrid | 95.45% | 90.91% | 82.95% | 100.00% | 100.00% | 0.082s |
| Hybrid + Reranker | 100.00% | 95.45% | 95.45% | 100.00% | 100.00% | 8.097s |
| **Dense + Reranker（当前生产）** | **100.00%** | **95.45%** | **95.45%** | **100.00%** | **80.00%** | **8.452s** |

Hybrid + Reranker 相对当前链路没有改善题或指标增量，且 q18 完全域外题因 BM25 普通词
命中而从空检索变成非空检索。因此部署门槛未通过，生产继续使用 `dense + reranker`；
Hybrid 能力保留为显式配置和后续扩大语料后的实验基线。完整指标口径、逐题结果与不部署
原因保存在 `eval/results/p2-1-hybrid-retrieval/`。

## P2-3 检索过程可视化

P2-2 Milvus 迁移是以混合检索确有收益为前提的条件项；P2-1 未通过部署门槛，因此本轮按
优化方案有意跳过 P2-2，继续保留 Chroma。P2-3 已在不改变原 `retrieve()` 契约的前提下，
为聊天链路增加可选的检索 Trace：

- 按实际执行链路动态展示 Dense、BM25、RRF、Reranker 和最终上下文阶段；
- 每个阶段保留排名、原始分数、过滤原因、是否最终入选等诊断字段；
- 完整 Chunk 在响应内按 `chunk_id` 去重，点击表格行后展示原文；
- 前端“检索详情”按钮默认关闭，展开状态在表格选择触发的 Streamlit 重跑后仍会保留；
- 分别记录查询向量化、Dense/Sparse、RRF、Reranker、上下文组装、LLM 和总耗时。

该能力默认关闭。开启后 API 会返回未入选候选的完整 Chunk，可能暴露知识库内容，只应在
可信的本地调试或受控测试环境使用：

```dotenv
RETRIEVAL_TRACE_ENABLED=true
```

真实生产索引验收问题“Chroma、Qdrant 和 Milvus 分别适合什么部署规模？”返回 1 条有效
引用，Trace 为 `Dense → Reranker → 最终上下文`，15 条 Dense 候选中 10 条通过阈值、
5 条被过滤，最终组装 5 chunks / 1187 tokens。预热 API 请求总耗时 `7860.0 ms`；重启后
浏览器冷启动请求为 `17291.4 ms`，主要耗时来自首次加载查询 Embedding 与 Reranker。
响应、截图和逐项验收记录保存在 `eval/results/p2-3-retrieval-trace/`。

## 环境准备

当前环境已在 Windows、Python 3.14.0 上验证。

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

编辑 `.env`，至少设置：

```dotenv
DEEPSEEK_API_KEY=your_key
# 可选：仅在可信调试环境开启检索过程详情
RETRIEVAL_TRACE_ENABLED=false
```

Embedding 使用 `local_files_only=True`。运行前需确保
`BAAI/bge-large-zh-v1.5` 已存在于本地 Hugging Face 缓存；这样服务启动和测试不会
因网络检查随机失败。该模型输出 1024 维向量，旧 384 维 Chroma 索引不能直接复用，
切换现有环境前必须重建索引或重新上传文档。

启用 `RERANKER_ENABLED=true` 时，还需预先缓存 `BAAI/bge-reranker-v2-m3`（约 2.14 GiB）。
Embedding 和 Reranker 都只从本地快照加载，不会在服务请求期间隐式联网。当前依赖固定
`transformers==4.57.6`，用于兼容 `FlagEmbedding==1.4.0` 的 encoder-only Reranker。

已有 SQLite 文档登记和原始上传文件时，使用安全重建脚本迁移到独立目录：

```powershell
.\.venv\Scripts\python.exe -m scripts.rebuild_index `
  --source-index-dir data/chroma_db `
  --target-dir data/chroma_bge_large_zh_v1_5 `
  --model BAAI/bge-large-zh-v1.5 `
  --expected-dimension 1024
```

校验成功后，再把 `.env` 中的 `EMBEDDING_MODEL` 和 `CHROMA_PERSIST_DIR` 一起切到新模型
与新目录。脚本不会覆盖旧索引或非空目标目录；旧索引可保留到人工回归完成后再清理。
parser 变化导致 chunk 数合理变化时，显式增加 `--accept-chunk-count-change`；脚本只会在
新索引完成数量、document_id 和维度校验后，事务同步 SQLite 登记。

## 启动

开发时分别启动：

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --reload-dir backend
.\.venv\Scripts\python.exe -m streamlit run frontend/app.py
```

或运行：

```powershell
.\scripts\start.ps1
```

一键脚本默认使用稳定模式，并将服务输出写入 `logs/`。只有需要边改后端边自动
重启时才开启热重载：

```powershell
.\scripts\start.ps1 -Reload
```

`start.ps1` 使用独立隐藏进程，脚本结束后 `Ctrl+C` 不会停止服务。优先使用启动输出中的
Backend/Frontend PID：

```powershell
Stop-Process -Id <Backend PID>,<Frontend PID>
```

若已找不到 PID，可按项目端口关闭：

```powershell
Get-NetTCPConnection -LocalPort 8000,8501 -State Listen |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ }
```

脚本会等待后端健康检查通过后再启动前端，避免页面先出现、后端尚未就绪的竞态。
如果当前 PowerShell 执行策略禁止直接运行 `.ps1`，使用一次性绕过命令（不会修改
系统执行策略）：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

上传时若出现连接中断，先查看 `logs/backend.error.log`；失败文件不会被前端标记
为已处理，也不会因普通页面刷新而自动重复提交，需点击“重试失败文件”。
当前选择全部入库成功后，上传框会自动清空；存在失败项时则保留选择和重试入口。

访问：

- 前端：http://localhost:8501
- API 文档：http://localhost:8000/docs

## 文档生命周期

每次上传生成独立 `document_id`：

```text
临时文件
  → SQLite processing 记录
  → parse/chunk/embed
  → Chroma 写入（metadata 含 document_id）
  → 原子移动为 document_id.ext
  → SQLite ready
```

同名文件会成为两个独立版本，不再依赖原始文件名生成 chunk ID。失败时清理临时文件和可能写入的向量，并保留 `failed` 记录用于排查。

## 测试

默认测试不会调用 DeepSeek，也不会写 `data/`：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

真实 API 测试必须显式开启：

```powershell
.\.venv\Scripts\python.exe -m pytest -m external --run-external -q
```

不依赖本地模型权重的 CI 测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not integration and not external" -q
```

## 评估

完整评估会创建隔离 Chroma、SQLite 和上传目录，但会调用 DeepSeek：

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe -m eval.eval run --label <label>
```

基于已有真实检索结果离线扫描阈值（不调用 DeepSeek）：

```powershell
.\.venv\Scripts\python.exe -m eval.eval threshold-sweep `
  eval/runs/<label>/raw_answers.jsonl `
  --label <sweep-label> `
  --thresholds 0.60 0.61 0.62 0.63 0.64 0.65 0.66 0.67 0.68 0.69 0.70 `
  --output-dir eval/runs/<sweep-label>
```

一次入库后比较多个 Reranker 候选数（不调用 DeepSeek）：

```powershell
$env:HF_HUB_OFFLINE='1'
.\.venv\Scripts\python.exe -m eval.eval retrieval-sweep `
  --label <sweep-label> `
  --candidate-ks 15 20 30
```

一次入库后执行 P2-1 五组混合检索消融（不调用 DeepSeek）：

```powershell
$env:HF_HUB_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
.\.venv\Scripts\python.exe -m eval.eval hybrid-ablation `
  --label <ablation-label>
```

对比两个 run：

```powershell
.\.venv\Scripts\python.exe -m eval.eval compare `
  eval/runs/baseline/report.json `
  eval/runs/<label>/report.json
```

每次只改变一个主要变量，label 与优化步骤对应。详细规则见 `eval/README.md`。

## 当前优化目标、实施方案与进度

### 优化目标

当前优化以“尽可能复用现有检索、评估和前端链路”为原则，重点解决以下问题：

1. **统一文档输入结构**：让不同格式都输出相同的数据模型，并保留标题、段落、列表、表格和来源位置等结构信息；
2. **扩大文档格式与测试集覆盖**：在现有 PDF、DOCX、TXT 基础上增加 Markdown、PPTX，并用更多真实文档和题型冻结 Dataset v3；
3. **升级来源原文展示**：从无格式纯文本升级为可恢复标题、段落、列表和表格结构的来源预览，并准确区分真实页与逻辑页；
4. **支持多轮对话**：在不污染独立问题检索的前提下，引入会话历史、问题改写和上下文窗口管理；
5. **维持可评估性**：每次迁移都通过固定数据集、单元测试和回归报告验证，不用主观体验代替指标。

### 具体优化方案

#### 1. 统一结构模型

使用 `ParsedBlock`、`ParsedUnit` 和 `SourceLocator` 作为所有文档格式的统一契约：

```text
文件
  → 格式 Adapter
  → BlockDraft
  → 公共 Assembler
  → ParsedUnit
```

公共 Assembler 统一负责稳定 `unit_id/block_id`、连续 `sequence`、`canonical_text` 和字符 offset，避免每种格式重复实现并产生不一致。

来源定位支持：

- `page`：PDF 真实页；
- `virtual_page`：DOCX/TXT 系统划分的逻辑页；
- `slide`：PPTX 幻灯片；
- `section`：Markdown 章节；
- `sheet`：为未来 Excel 支持预留的工作表及单元格范围；本轮不实现 Excel。

前端后续必须使用 `location_label` 展示来源，不能把所有数字都称为“页码”。

#### 2. 旧格式 Adapter 迁移

先把 PDF、DOCX、TXT 迁移到统一模型，再修改生产链路。DOCX 额外保留标题层级、`heading_path`、列表层级和二维表格；TXT 使用固定行数生成逻辑页；PDF 保留真实页定位。

#### 3. 全链路数据模型迁移

按以下顺序把 `ParsedUnit` 接入现有 RAG 链路：

```text
parser registry / upload
  → chunker
  → Chroma metadata 与 SQLite document_units
  → citation
  → source preview
  → eval
```

迁移完成后不再保留旧 page-dict 作为最终兼容格式，数据库读取的结构 JSON 也必须重新经过 Pydantic 校验。

#### 4. 新格式支持

在全链路稳定后增加：

1. Markdown：标题路径、段落、列表、引用、代码块；
2. PPTX：幻灯片标题、文本框、列表、表格和 slide 定位；
3. 扫描 PDF：作为可选增强项，单独评估 OCR 复杂度、准确率和运行成本后再决定是否纳入主链路。

Excel、旧 PowerPoint/Excel、OCR、图表理解、SmartArt 和演讲者备注不在本轮范围内；其中 Excel 只保留 locator 扩展位，不设置实现或验收项。

#### 5. 来源预览与多轮对话

来源预览根据 block 类型恢复基础结构，并基于 chunk offset 高亮命中内容。多轮对话采用“历史消息存储 + 当前问题改写 + 独立检索 + 有界上下文”的方式，避免直接把全部历史拼进检索查询。

#### 6. Dataset v3

Dataset v3 固定为单一顶层 JSON。模型生成的候选题只提供文档名和证据片段，项目内
`prepare` 工具再根据确定性解析结果补齐 parser 版本、稳定 eval unit ID、字符 offset、
snippet hash 和完整 locator；零命中或多位置命中必须通过显式 evidence override 处理，
工具不会自动把人工审核状态改为 `verified`。

当前候选集已完成 prepare 和一次代码辅助的全量语义审计，但尚未冻结：审计修正了两处
题意/答案表述问题，以及一处因 PPTX 组合形状漏解析而被误判为不可回答的问题；同时增加
了 keypoint 必须能由标准答案命中的校验，避免 gold answer 在 Strict Accuracy 下自相矛盾。
冻结前仍需真实人工逐项核对问题、标准答案、证据和全部不可回答题的 whole-corpus 检查。
当前 52 个 answer item 中有 43 个可回答、9 个不可回答；不可回答项由 5 个
`out_of_scope` 和 4 个 `insufficient_detail` 组成。5 个域外题已通过确定性解析文本的
whole-corpus 关键词排查，但仍需在冻结前由真实审核者签核。

```powershell
.\.venv\Scripts\python.exe -m eval.dataset_v3 validate `
  --dataset eval/dataset/questions_v3.json `
  --corpus eval/corpus `
  --manifest eval/dataset/corpus_v3_manifest.json

# 导出与当前数据集哈希绑定的逐项人工审核包
.\.venv\Scripts\python.exe -m eval.dataset_v3 review-export `
  --dataset eval/dataset/questions_v3.json `
  --output eval/dataset/questions_v3_review.md

# 只在人工审核字段全部完成后才应通过
.\.venv\Scripts\python.exe -m eval.dataset_v3 validate `
  --dataset eval/dataset/questions_v3.json `
  --corpus eval/corpus `
  --manifest eval/dataset/corpus_v3_manifest.json `
  --strict
```

未冻结数据只能运行显式预检。v3 评测入口会校验 manifest，展开 17 个 dialogue turn，并让
它们使用 gold `standalone_question`；这只验证下游检索，不代表追问改写服务已经实现。

```powershell
$env:HF_HUB_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
.\.venv\Scripts\python.exe -m eval.eval retrieval-sweep `
  --label v3-structured-preflight `
  --dataset eval/dataset/questions_v3.json `
  --corpus eval/corpus `
  --candidate-ks 15 `
  --allow-draft-v3
```

### 当前做到哪一步

当前开发分支为 `phase1/unified-structure-model`。**五种范围内格式的统一结构解析、结构化 chunk、版本化 SQLite schema、`document_units` 和 Chroma 轻量定位 metadata 已在隔离测试链路贯通；23 份真实语料已全部完成确定性解析，PPTX 组合形状中的原生文字也已纳入。当前生产上传、引用 API 和前端仍使用旧 page-dict / `document_pages` 链路，尚未切换，也未清理现有生产数据。**

| 工作项 | 当前状态 |
|---|---|
| `ParsedBlock`、`ParsedUnit`、`SourceLocator` 统一模型 | 已完成 |
| 稳定 ID、sequence、canonical text、offset 校验 | 已完成 |
| 公共 `BlockDraft → Assembler → ParsedUnit` 组装层 | 已完成 |
| PDF Adapter | 已完成代码迁移 |
| DOCX Adapter（标题、列表、表格、heading path、逻辑页） | 已完成代码迁移 |
| TXT Adapter（逻辑页、段落拆分） | 已完成代码迁移 |
| Adapter registry | 已支持五种格式及解析 warning；尚未接入旧生产上传入口 |
| 模型与五种 Adapter 专项测试 | 已完成，本地全量 pytest 已通过；仍需 CI 结果 |
| DOCX/TXT Adapter 专项测试 | 已完成 |
| `ParsedUnit` 原生 chunker | 已完成隔离链路；旧生产入口仍调用 legacy `chunk_document` |
| 新 SQLite schema 与 `document_units` | 已完成 bootstrap、版本拒绝、事务替换、读取重校验和级联测试；尚未启用生产库 |
| Chroma 结构化 metadata | 已完成标量编码和隔离写入测试；尚未写入生产 collection |
| Markdown Adapter | 已完成 fixture 与 3 份真实 Markdown 验收 |
| PPTX Adapter | 已完成标题、文本框、嵌套组合形状、列表、表格和无文本 warning 测试，并通过 17 份真实 PPTX 验收 |
| DOCX/PPTX ZIP 资源边界 | 已完成 entry 数、单项 XML 和总解压大小校验 |
| 结构化引用预览服务与安全 renderer | 已完成隔离实现和测试；尚未接入旧 API/UI |
| 生产上传/API/UI 切换与旧数据清理 | 尚未开始；必须在真实格式与预览验收后单独执行 |
| Dataset v3 prepare | 已把 6 个候选批次合并并追加 5 道域外题；35 道单题、6 组对话（17 轮）、80 条 evidence 均已定位，manifest 已生成 |
| Dataset v3 语义预审 | 已完成代码辅助的 52 个 answer item 全量检查，修正 3 处语义问题，补齐 48 个原先无法命中标准答案的 keypoint 变体，并为 5 道域外题执行 whole-corpus 关键词排查 |
| Dataset v3 structured preflight | 已贯通 manifest → 五格式解析 → 2,438 units → 2,584 chunks → 隔离 Chroma → 52 项检索；Hit/Recall 97.67%、MRR 95.35%，1 道可回答题漏检；真实 LLM 尚未授权运行 |
| Dataset v3 人工审核与冻结 | 审核包已生成，但真实人工签核尚未完成；当前全部 review 保持 `draft`，strict validate 会按设计失败 |
| 多轮对话 | 尚未开始 |

因此，当前最准确的阶段结论是：

> **阶段 1 的统一结构、真实格式解析、结构化 preview/renderer 和 Dataset v3 prepare 已贯通。下一步先人工审核并冻结 v3，再用它验收生产上传/API/UI 的结构化切换。当前运行时仍使用原有 `backend/services/parser.py`、`document_pages` 和 page-dict 链路；在阶段 2 验收和明确的数据清理确认前，不切换生产库。**

所有简历指标都以实际 eval 报告为准，不预先填写目标值。
