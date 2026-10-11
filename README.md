# 中文个人本地知识库 RAG

面向中文文档的知识库检索与问答系统。项目将多格式文档解析为统一结构，结合向量检索、关键词检索与可选重排序生成回答，并提供可定位的来源引用、结构化原文预览和检索过程追踪。

当前以 **Windows 可信本地环境中的源码运行**为主要使用方式：React 提供会话、知识库和设置三个工作区，FastAPI 提供 API 并托管前端构建产物。本地完成文档存储、Embedding 与重排序；启用问答生成或视觉识别时调用配置的模型 API。

[功能与边界](#功能与边界) · [数据链路与存储](#数据链路与存储) · [项目结构](#项目结构) · [快速上手](#快速上手) · [测试与评测](#测试与评测) · [文档导航](#文档导航)

## 功能与边界

| 能力 | 当前实现 |
|---|---|
| 文档入库 | 支持 PDF、DOCX、TXT、Markdown、PPTX，统一保留结构单元、来源位置、稳定 ID 和字符偏移；提供上传队列、失败重试、文档搜索与删除 |
| 检索问答 | 支持 Dense、BM25、Hybrid 三种模式；Hybrid 使用 RRF 融合双路候选，可用 BGE Reranker 重排，并控制最终 Top K 与单文档片段数量 |
| 来源追溯 | 通过本轮稳定的 `【来源N】` 编号映射真实检索结果，支持结构化全文预览、命中高亮、自动定位与 OCR 原图对照 |
| 视觉识别 | 可选扫描 PDF 页 OCR、PDF 页面图片说明及 DOCX/PPTX 内嵌图片识别，包含结果缓存、调用预算、超时重试与失败警告 |
| 会话交互 | 持久化多轮会话、追问改写、有界历史、NDJSON 流式输出、独立推理内容展示、停止生成、失败重试与请求幂等回放 |
| 知识组织 | 层级标签管理，父标签包含子孙标签，多标签按并集筛选；同时用于知识库管理和问答检索范围 |
| 配置与诊断 | 页面调整问答、检索和视觉设置；记录请求配置版本、候选分数、筛选原因与阶段耗时，支持持久化 Trace |
| 索引维护 | Chroma 持久化向量；支持内存 BM25 与持久化 BM25S 内存映射后端，以及索引失效标记、后台重建、校验和活动版本切换 |

使用与实现边界：

- **使用范围**：尚无账号、角色或文档权限体系，接口面向可信本地环境。远程或多人部署需要补充身份认证与访问控制。
- **数据流向**：开启生成会发送问题、选定文档片段及有界会话历史；开启视觉识别会发送相应图片。关闭这些功能后可以只运行本地检索，关闭生成并不自动关闭视觉识别。
- **格式能力**：不支持 Excel/CSV、旧版 Office 格式、Markdown 外部图片、SmartArt 和演讲者备注。DOCX/TXT 使用逻辑页，不能等同于原文件真实分页；PDF 以逐页文本提取为基础，视觉识别不等同于复杂版面或扫描表格恢复。
- **答案可靠性**：来源编号与预览便于核对证据，但不自动证明证据支持结论。专用拒答门控尚未开放；Dense 距离阈值和无结果短路不能保证识别所有不可回答问题。
- **运行规模**：当前一致性协调主要在单个服务进程内完成。后台稀疏索引维护采用全量重建；失效、缺失或不匹配的持久化索引会显式报错，不自动降级或保证无中断。
- **发布状态**：当前公开仓库提供源码运行方式，服务托管与启动流程见[开发说明](new_docs/开发/DEV-012-服务托管与启动停止.md)。
- **公开数据范围**：仓库不附带课程资料、测试文档、评测语料、题库、原始检索结果或截图。使用者需要自行准备有权使用的文档；这些本地文件和业务数据应保持在 Git 忽略范围内。

## 技术栈

| 层次 | 主要组件 |
|---|---|
| 后端与结构模型 | Python、FastAPI、Pydantic、SQLite |
| 检索与本地模型 | ChromaDB、Sentence-Transformers、FlagEmbedding、PyTorch、Jieba、rank-bm25、BM25S |
| 文档处理 | PyPDF2、python-docx、python-pptx、markdown-it-py、pypdfium2、Pillow |
| 生成与视觉接口 | OpenAI SDK，连接 DeepSeek 或其他 OpenAI 兼容模型服务 |
| 主前端 | React、TypeScript、Vite、Ant Design、React Router、原生 Fetch 流读取 |
| 验证 | pytest、Vitest、Testing Library；隔离评测与索引校验脚本 |

默认模型标识为 `BAAI/bge-large-zh-v1.5` 和 `BAAI/bge-reranker-v2-m3`；前者用于 Embedding，后者在启用重排序时使用。具体依赖版本以 [requirements.txt](requirements.txt)、[web/package.json](web/package.json) 和前端锁文件为准。

## 数据链路与存储

### 文档入库

```text
上传文件与可选标签
  → 分配 document_id，保存临时文件，登记 processing 状态
  → 格式适配器解析；按配置执行 OCR / 图片说明
  → 统一 ParsedUnit / ParsedBlock / SourceLocator
  → 组装规范文本、稳定 ID 和字符偏移
  → 生成带来源范围的文本块
  → SQLite 保存结构单元
  → BGE Embedding → Chroma 保存文本块与定位元数据
  → 保存标签，原子移动原文件，标记 ready
  → 按稀疏后端配置触发后台索引重建
```

当前结构契约为 `structured-v2`。切块支持 `fixed` 与 `recursive`；`recursive` 结合段落、句子边界、字符窗口与 tokenizer 上限控制片段长度，保留原始规范文本中的字符范围，且不跨来源单元。

每次上传创建独立文档 ID，同名文件不会覆盖旧文档。入库失败时执行补偿清理并保留失败记录；原文件、SQLite 与 Chroma 的协调并非跨存储 ACID 事务。删除以文档 ID 清理关联记录和向量。

### 检索与生成

```text
当前问题 + 标签范围 + 有界会话历史
  → 可选追问改写（失败时使用原问题）
  → Dense / BM25 / Hybrid 召回
      Dense：问题向量 → Chroma → 距离阈值过滤
      BM25：中文分词 → 词法召回
      Hybrid：两路候选 → 按 chunk_id 去重 → RRF 排名融合
  → 可选 Cross-encoder 重排序
  → 单文档片段限额与最终 Top K
  → 带来源编号的上下文
  → 模型 API 生成，或关闭生成后直接返回检索来源
  → NDJSON 事件流 → 来源列表 → 成功结果与可选 Trace 持久化
```

每个新问题重新检索；历史用于理解指代和保持表达连贯，来源编号仅对应本轮结果。流式协议依次传输 `meta`、`reasoning_delta` / `delta`、`citations` 和 `done`，失败使用 `error` 事件。中断或失败不将半截内容保存为成功答案。

引用预览按文档 ID、单元 ID、解析版本与字符范围回查结构，验证文本切片后高亮来源。列表中的“正文已引用 / 未引用”只表示回答是否使用该编号，不是证据有效性的自动评分。

### 存储职责

| 位置 | 保存内容与用途 |
|---|---|
| `data/uploads/` | 原始文档，以文档 ID 命名 |
| `data/metadata.db` | 文档状态、结构单元、标签、会话、运行时设置和受容量限制的 Trace |
| `data/chroma_db/` | 向量、文本块与检索所需的轻量来源元数据 |
| `data/sparse_index/` | BM25S 索引版本、校验清单与活动指针，仅持久化稀疏后端使用 |
| `data/vision_cache/`、`data/ocr_images/` | 视觉结果缓存及用于原图对照的图片 |
| `data/runtime_secrets.bin` | 默认数据库路径下，页面保存的模型密钥的 Windows 用户级 DPAPI 密文 |
| Hugging Face 缓存 | 本地 Embedding / Reranker 模型；下载与运行需使用同一缓存目录 |
| `logs/` | 源码启动脚本的日志与进程记录 |

主数据与缓存路径可以通过环境变量调整。备份需要同时考虑原文件、结构化元数据和相关索引，不能只复制 Chroma。详细步骤见[备份恢复与索引重建](new_docs/运维/OPS-002-数据备份恢复与索引重建.md)。

## 项目结构

```text
backend/
  main.py                    FastAPI 入口、启动初始化与 SPA 托管
  config.py                  部署默认配置与校验
  models/                    文档结构、接口与运行时设置模型
  routers/                   上传、问答、会话、引用、标签、设置 API
  services/
    parsers/                 五格式适配器、公共组装器与可选 OCR
    chunker.py               带来源偏移的文本切块
    retriever.py             召回、融合、重排与最终上下文选择
    sparse_*.py              稀疏检索、生命周期与后台重建
    bm25s_store.py           持久化索引构建、加载与发布
    *_store.py               文档、会话、结构单元、标签与配置存储
web/
  src/pages/                 chat / knowledge / settings 三个工作区
  src/components/            结构渲染、来源预览、Trace 与标签组件
  src/api/                   API 类型、请求与 NDJSON 流解析
  src/__tests__/             前端测试
frontend/                    保留的旧 Streamlit 兼容前端
scripts/                     源码启停、索引重建与专项验证
tests/                       后端单元与集成测试
eval/                        隔离评测源码、指标与使用说明；数据由本地准备
new_docs/                    按需求、设计、开发、测试与运维组织的文档中心
docs/                        历史方案、反馈与操作记录
```

阅读代码可从 `backend/main.py` 进入，再沿 `routers/upload.py` 和 `routers/chat.py` 查看两条主链路；前端入口为 `web/src/App.tsx`。

## 快速上手

以下步骤面向**全新源码目录和新空库**，使用 CPU 跑通基础链路。已有部署请保留自己的 `.env`、数据和索引配置，不要用示例覆盖。

### 1. 准备环境与依赖

需要 Python 3.14、Node.js 24.x 和 npm。Python 版本与仓库依赖验证基线一致；前端锁文件包含要求 Node.js 22.19 或更高版本的依赖。以下命令均在仓库根目录的 PowerShell 中执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install torch==2.13.0 --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check

Push-Location web
npm.cmd ci
Pop-Location
```

### 2. 准备本地模型

服务以 `local_files_only=True` 加载模型，不会在启动或提问时自动下载。首次运行前下载到 Hugging Face 标准缓存；`.env.example` 与下述上手配置启用 Reranker，因此准备两个模型：

如需自定义 `HF_HOME` 或 `HF_HUB_CACHE`，先在当前 PowerShell 设置环境变量；下面的下载命令不会自行读取项目 `.env`。

```powershell
$env:HF_HUB_OFFLINE = "0"
.\.venv\Scripts\python.exe -c "from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-large-zh-v1.5')"
.\.venv\Scripts\python.exe -c "from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-reranker-v2-m3')"
```

下载需要网络和模型存储空间。若自定义 `HF_HOME` 或 `HF_HUB_CACHE`，下载和启动必须使用相同配置。资源有限时可关闭 Reranker，届时只需准备 Embedding 模型。

### 3. 创建并调整配置

```powershell
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item -LiteralPath .env.example -Destination .env
}
```

编辑新目录的 `.env`，覆盖以下字段。先关闭外部模型调用，使用 Dense 与内存 BM25 后端；保留重排，并在可信本地环境开启来源预览与 Trace：

```dotenv
LLM_ENABLED=false
DEEPSEEK_API_KEY=
RETRIEVAL_MODE=dense
SPARSE_BACKEND=rank_bm25_memory
SPARSE_INDEX_CORPUS_ID=
SPARSE_AUTO_REBUILD=false
CHUNK_STRATEGY=recursive
EMBEDDING_DEVICE=cpu
RERANKER_ENABLED=true
RERANKER_DEVICE=cpu
RERANKER_USE_FP16=false
CITATION_PREVIEW_ENABLED=true
RETRIEVAL_TRACE_ENABLED=true
OCR_ENABLED=false
```

`.env.example` 中的持久化稀疏后端与语料标识来自既有部署，新空库不能直接照搬。上述内存后端无需预构建 BM25S 索引；跑通后可在设置页切换 Hybrid，体验关键词与向量混合检索。

如需生成答案，在设置页保存有效模型连接和密钥并启用生成，或在首次启动前配置 `LLM_ENABLED=true` 与有效的 `DEEPSEEK_API_KEY`。视觉识别另行启用，所选视觉模型必须支持图片输入。

### 4. 启动并验证基础链路

```powershell
.\scripts\start.ps1 -InferenceProfile cpu
```

脚本使用 `.venv`，重新构建 React，启动 FastAPI，并在模型预热就绪后打开页面：

- [主页面](http://localhost:8000/)：进入知识库上传小型 TXT 或 Markdown 文件，再到会话页提问并查看来源。
- [API 文档](http://localhost:8000/docs)：查看请求与响应结构。
- [健康接口](http://localhost:8000/api/health)：查看存活及模型预热状态。

关闭生成时，页面返回相关文档来源而不调用回答模型。停止服务：

```powershell
.\scripts\stop.ps1
```

### 5. 前后端开发

前后端分别启动时，后端终端在根目录运行：

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --reload-dir backend
```

另一终端启动 Vite：

```powershell
Set-Location web
npm.cmd run dev
```

访问 [Vite 开发页面](http://localhost:5173/)，`/api` 请求由 Vite 代理到后端 8000。不要同时运行另一份 `start.ps1` 后端。已有 `web/dist` 时，8000 可能展示旧构建；源码开发页面以 Vite 为准。

### 6. 可选 GPU 环境

GPU 使用独立的 `.venv-gpu`，避免替换 CPU 环境。仓库使用的 CUDA PyTorch 安装组合如下，新机器需要匹配驱动并验证：

```powershell
python -m venv .venv-gpu
.\.venv-gpu\Scripts\python.exe -m pip install torch==2.13.0 --index-url https://download.pytorch.org/whl/cu130
.\.venv-gpu\Scripts\python.exe -m pip install -r requirements.txt
.\.venv-gpu\Scripts\python.exe -m pip check
.\scripts\start.ps1
```

`start.ps1` 默认选择 GPU，覆盖运行进程的模型设备为 `cuda:0`，Reranker 使用 FP16；CPU 模式使用 CPU / FP32。可附加 `-Reload`、`-NoBrowser`，旧 Streamlit 仅在显式指定 `-LegacyFrontend` 时启动。更多环境说明见[源码启停指南](new_docs/运维/OPS-001-环境配置与服务启停.md)。

## 配置与开发约定

- **配置优先级**：部署默认来自 `backend/config.py` 与环境变量，页面保存的运行时覆盖值优先。请求开始时冻结配置快照，保存后的设置用于后续请求；修改 `.env` 后也要检查页面是否保留旧覆盖。
- **配置生效范围**：问答和检索设置影响后续请求；视觉设置影响后续上传或重新解析，旧文档不会自动补做 OCR。更换 Embedding 模型或切块规则需要重建派生索引。
- **来源契约**：新增格式应通过 `backend/services/parsers/` 的适配器和公共组装器输出统一结构，保持定位、规范文本、稳定 ID 与偏移一致，避免另建一条与预览脱节的解析链路。
- **持久化 Sparse 首代**：切换 `bm25s_mmap` 前需受控构建并发布匹配的首个活动索引，后台不自动初始化缺失的首代。发布后再配置后端、语料标识与自动重建开关；索引合同中的解析、切块与 BM25 参数必须匹配。完整步骤见[索引重建](new_docs/运维/OPS-002-数据备份恢复与索引重建.md#rebuild)。
- **当前运行依赖**：持久化 Sparse 后台重建仍从 `eval/crud_rag_1000/v3_1/questions_v3_120q.json` 读取 120 道验证问题。公开仓库不附带该文件，使用者需按代码要求自行准备本地验证题库；否则不能直接使用自动重建。首次运行使用上文的 `rank_bm25_memory` 配置。缺失、失效或错配的索引会使相关检索显式失败，需要查看状态并重建或主动调整模式。
- **密钥与诊断数据**：页面保存的密钥使用当前 Windows 用户的 DPAPI 加密，读取接口不回填明文；环境密钥仍由部署者自行管理。Trace 与原文预览可能包含完整文档内容，应按实际使用范围控制开关。
- **数据维护**：索引重建、恢复或升级先备份并在独立目录验证。恢复页面默认只删除运行时覆盖，不修改 `.env`，也不清空知识库。

## 测试与评测

公开仓库保留测试代码，但不附带 `tests/fixtures/` 与 `eval/` 下的测试数据。公开 CI 与下列命令排除依赖固定文档的测试模块、真实 PDF 文件测试、依赖固定题库的稀疏索引重建测试及数据集验收测试；其余测试覆盖检索、会话、存储、适配器逻辑与配置等独立行为：

```powershell
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m pytest tests -m "not integration and not external" -q `
    --ignore=tests/test_api.py --ignore=tests/test_parser.py `
    --ignore=tests/test_structured_ingest.py --ignore=tests/test_hybrid_dataset.py `
    --ignore=tests/test_sparse_rebuild.py `
    --deselect=tests/test_pdf_adapter.py::test_pdfium_engine_reads_real_pdf_fixture `
    --deselect=tests/test_rebuild_sparse_index.py::test_controlled_build_on_isolated_real_storage `
    --deselect=tests/test_sparse_lifecycle.py::TestUploadLifecycleIntegration
```

已经准备本地测试资料时，可运行完整后端与评测测试；本地模型集成及真实 API 调用仍需单独启用：

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not integration and not external" -q -p no:cacheprovider
```

本地模型集成测试需要准备缓存；`external` 测试默认跳过，真实 API 调用需要显式启用。普通后端测试使用隔离目录，避免读写业务数据。测试范围与外部调用方式见[测试文档](new_docs/测试/README.md)。

前端检查：

```powershell
Push-Location web
npm.cmd test
npm.cmd run lint
npm.cmd run build
Pop-Location
```

评测代码提供隔离入库、检索扫描、模式消融、答案与引用指标及报告对比。先区分检索评测和实际模型生成，再选择数据集与命令；检索指标不能直接当作回答准确率，不同数据集和配置的结果也不能直接比较。

评测命令仍会读取本地数据文件，公开源码目录不能直接运行默认数据集。数据结构与命令见[评测 CLI 文档](eval/README.md)，历史测试口径和调参记录见[评测验收说明](new_docs/测试/TEST-006-数据集与评测验收.md)及[优化文档](new_docs/优化/README.md)。这些记录描述当时的本地环境；原始测试资料与逐题结果不随公开仓库发布。

## 文档导航

| 阅读目的 | 入口 |
|---|---|
| 按主题浏览完整文档 | [文档中心](new_docs/README.md) |
| 理解整体架构与数据职责 | [系统架构与存储边界](new_docs/设计/DES-001-系统架构与存储边界.md) |
| 扩展格式、修改解析或入库 | [五格式结构化解析](new_docs/开发/DEV-001-五格式结构化解析.md)、[上传与索引生命周期](new_docs/开发/DEV-002-上传与索引生命周期.md) |
| 理解检索与上下文选择 | [检索与模型推理](new_docs/开发/DEV-004-检索与模型推理.md)、[检索融合与上下文选择](new_docs/设计/DES-003-检索融合与上下文选择.md) |
| 修改会话、流式输出与引用 | [会话流式问答](new_docs/开发/DEV-005-会话流式问答.md)、[来源引用与结构预览](new_docs/开发/DEV-006-来源引用与结构预览.md) |
| 配置视觉、设置与 Trace | [OCR 识别与原图预览](new_docs/开发/DEV-003-OCR识别与原图预览.md)、[运行时设置与密钥](new_docs/开发/DEV-009-运行时设置与密钥.md)、[检索 Trace](new_docs/开发/DEV-010-检索Trace与配置追溯.md) |
| 开发前端 | [React 主前端](new_docs/开发/DEV-008-React主前端.md)、[web 使用说明](web/README.md) |
| 启停、备份、恢复与故障排查 | [环境配置与服务启停](new_docs/运维/OPS-001-环境配置与服务启停.md)、[数据备份恢复与索引重建](new_docs/运维/OPS-002-数据备份恢复与索引重建.md)、[设置密钥与故障排查](new_docs/运维/OPS-003-设置密钥与故障排查.md) |
| 运行评测、查看测试与优化证据 | [评测说明](eval/README.md)、[测试目录](new_docs/测试/README.md)、[优化目录](new_docs/优化/README.md) |
| 理解服务托管与启动流程 | [服务托管与启动停止](new_docs/开发/DEV-012-服务托管与启动停止.md) |

`new_docs/` 按主题维护实现说明、验证边界与运维步骤；`docs/` 保留历史方案，`eval/` 保留评测源码与说明。查看历史记录时，先核对其数据集、配置、时间与实际代码，避免将旧实验结论当作当前默认行为；文中提到的本地数据与报告路径不代表公开仓库附带这些文件。
