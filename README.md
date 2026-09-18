# 中文企业知识库 RAG

一个以“可评估、可追溯、可复现”为核心的中文 RAG 项目。系统通过统一文档结构处理
PDF、DOCX、TXT、Markdown 和 PPTX，完成解析、切分、向量检索、Cross-encoder
重排和 DeepSeek 问答，并使用稳定的 `【来源N】` 协议返回可定位到原文结构的引用。

## 当前状态

截至 2026-09-18，以下链路已经贯通：

- 验收轮新增：知识库按文档名称模糊搜索（与标签筛选组合）；
- OCR 文本做确定性结构分类（独立标题/列表块 + 混合段落首行标题渲染），
  全文预览与引用预览不再把 OCR 内容全部压成普通段落；
- OCR 原图对照预览：OCR 块旁展示被识别的原始图片（可放大），
  配置 `OCR_IMAGE_PREVIEW_ENABLED` 与 `OCR_IMAGE_DIR`；
- eval 指标去除 structured-v1 硬编码，structured-v2 的 MD/PPTX 引用可正确统计；
- 生产 SQLite 与 Chroma 均已 100% 为 structured-v2。

- 五种文档格式统一输出 `ParsedBlock`、`ParsedUnit` 和 `SourceLocator`；
- 公共 Assembler 负责稳定 ID、连续 sequence、canonical text 和字符 offset；
- 生产上传和索引重建均使用 `structured-v2`；可选 OCR/图片理解产出进入 canonical text，
  并以 `attributes.ocr` 标记来源；
- Chroma 保存检索所需的轻量定位 metadata，SQLite schema v5 保存文档、结构化 unit、
  标签、会话、模型思考内容和受容量限制的持久化 Trace；
- Dense 检索使用 `BAAI/bge-large-zh-v1.5`，可选
  `BAAI/bge-reranker-v2-m3` 进行 Cross-encoder 重排；
- 引用预览按 unit 原序恢复整份文档结构并高亮命中区间；
- 支持持久化多轮对话、NDJSON 流式回答、停止/失败重试、追问改写、有界历史、
  幂等重放和 409 并发冲突重同步；
- 标签支持父子路径、父级含子孙和多标签并集，可同时用于问答检索范围与文档管理；
- React SPA 已成为 8000 端口的主前端，覆盖会话、检索范围、引用/OCR 预览、Trace、
  文档上传、标签管理和文档维护，并通过桌面、缩放等效视口与移动端验收；
- 视觉模型可跟随问答模型或独立配置，并分别控制文字识别、图片描述的思考模式、
  思考强度、Temperature、输出长度和超时；设置页支持真实图片连接测试；
- OCR/视觉失败会区分限流、超时、空输出和截断，记录服务商请求 ID/错误码；单进程视觉
  请求串行执行并沿用有限重试，便于核对免费额度或账号级并发限制；
- 文档预览将同一原图及其 OCR 文本视为一个布局组，组结束后正文恢复整行，避免文字
  受不定高度浮动图片影响；
- 来源引用会在预览抽屉完成动画和图片布局后重新定位命中标记；模型思考按独立流展示，
  正文开始时自动折叠，历史消息可再次展开；
- `scripts/start.ps1` 默认重建并启动 React SPA，打开带缓存戳的 8000 地址；旧 Streamlit
  仅在显式指定兼容参数时启动；
- 检索 Trace 可持久化并展示 Dense、BM25、RRF、Reranker、最终上下文、阶段耗时、
  settings revision、本次实际检索参数和不含密钥的 LLM 调用摘要。

当前仍未完成的正式验收项：

- Dataset v3 的 41 个审核组仍为 `draft`，尚未由真实审核者逐项签核；
- 自动关键点评分器对 Markdown、代码符号、空格和同义改写过于敏感；
- 不可回答题仍出现过一次“正确拒答但附带引用”的协议问题；
- CPU Reranker 的检索延迟仍然较高。

### 设置接口的当前安全边界

后端已提供 `GET /api/settings`、`GET /api/settings/runtime-status`、
`PATCH /api/settings`、`POST /api/settings/reset` 和 `POST /api/settings/test-llm`。
React 主前端已增加 `/settings` 路由和导航入口，可以读取脱敏配置与运行状态、保存问答
LLM、Retrieval 设置和 DPAPI Key、恢复部署默认，并用未保存草稿执行一次最小连接测试。检索设置
会在保存后的下一请求生效，Dense、BM25、RRF、Reranker 与最终 Top K 使用各自参数。默认
`SPARSE_BACKEND=rank_bm25_memory`时，BM25 k1/b变化刷新进程内缓存；部署级选择
`bm25s_mmap`时，k1/b必须与预构建generation合同一致，否则显式失败。两者都不重写生产
向量库。读取接口不会返回
API Key 明文，也不会为查看状态而冷加载模型；敏感字段的 422 校验错误同样不会回显输入。

当前项目没有账号、角色或权限体系，设置接口只适用于可信本地工作空间。不要把后端直接
暴露到不受信任的网络；如需远程或多人使用，应先在反向代理或应用层增加身份认证和访问
控制。连接测试会真实请求所填模型服务，可能产生少量费用，只应由用户显式触发。
设置页不使用 localStorage 保存配置，API Key 输入框不会回填旧明文；恢复默认只删除运行时
覆盖和当前用户 DPAPI 密钥，不修改 `.env`、不清空或重建向量库。启用
`RETRIEVAL_TRACE_ENABLED` 后，新问答 Trace 会记录该请求冻结的 revision、实际 Top K/候选深度/
Dense threshold/BM25/RRF/Reranker 参数，以及模型是否实际调用和 provider/model；不会记录 API Key。
旧持久化 Trace 继续可读，并在 Drawer 中标注“旧 Trace 未记录”。拒答门控仍未开放，
需要先完成 Dataset v3 的 V3-5 校准。视觉与会话优化的完整记录及剩余事项见
[`docs/2026-09-18-视觉与会话体验优化记录.md`](docs/2026-09-18-视觉与会话体验优化记录.md)。

## 当前架构

```text
React SPA（主前端，FastAPI 托管 web/dist）
   │ /api
   ▼
FastAPI
   ├── upload
   │     file
   │       → format adapter
   │       → optional OCR / image understanding
   │       → ParsedUnit
   │       → structured chunker
   │       → BGE embedding
   │       → Chroma
   │       └── SQLite documents / document_units / tags
   │
   ├── chat
   │     question + tag scope + bounded conversation history
   │       → standalone question rewrite
   │       → Dense / BM25 / Hybrid retrieval
   │       → optional Reranker
   │       → DeepSeek
   │       → NDJSON stream + source-ID citations
   │
   └── citation/document preview / conversations / persistent retrieval trace

legacy Streamlit（兼容对照，尚未删除）
   └── HTTP → FastAPI

eval
   └── isolated corpus + Chroma + SQLite
         → Dataset v3 validation
         → retrieval / answer / citation metrics
         → report and review packet
```

核心目录：

```text
backend/                  FastAPI、数据模型和 RAG 服务
backend/services/parsers/ structured-v2 五格式 Adapter 与可选 OCR
web/                      React 19 + TypeScript + Ant Design 6 主前端
frontend/                 兼容保留的旧 Streamlit 前端
eval/                     数据集、隔离评测 CLI、历史结果
tests/                    unit / integration / external 测试
scripts/rebuild_index.py  安全索引重建
docs/                     设计、操作指南和项目说明
```

## 文档结构与定位

所有格式使用同一套结构模型，但保留格式自身的定位语义：

| 格式 | locator | 主要结构 |
|---|---|---|
| PDF | `page` | 真实页、段落 |
| DOCX | `virtual_page` | 标题路径、段落、列表、表格 |
| TXT | `virtual_page` | 固定物理行窗口、段落 |
| Markdown | `section` | 标题路径、段落、列表、引用、代码块 |
| PPTX | `slide` | 标题、文本框、组合形状、列表、表格 |

前端统一使用 `location_label` 展示来源，不把逻辑页、幻灯片或章节错误地称为“页码”。
SQLite 中保存的结构 JSON 在读取时会重新经过 Pydantic 校验；损坏或版本不兼容的数据会被拒绝。

可选 OCR 支持扫描 PDF、PDF 页内图片/图表以及 DOCX/PPTX 内嵌图片；各层默认关闭，
失败只产生 warning，不阻塞其余内容入库。当前不支持 Excel、旧版 PowerPoint/Excel、
Markdown 外部相对路径图片、SmartArt 和演讲者备注。
OCR 表格转 Markdown 已推迟到下一轮；当前 OCR 表格内容仍以段落原文展示。

## 环境准备

当前 GPU 默认环境已在 Windows、Python 3.14.0、RTX 4060 Laptop GPU 上验证。
原 `.venv` 作为 CPU 回退环境保留；日常启动使用独立的 `.venv-gpu`。

```powershell
D:\Python\python.exe -m venv .venv-gpu
.\.venv-gpu\Scripts\python.exe -m pip install --no-cache-dir `
  torch==2.13.0 --index-url https://download.pytorch.org/whl/cu130
.\.venv-gpu\Scripts\python.exe -m pip install --no-cache-dir -r requirements.txt
.\.venv-gpu\Scripts\python.exe -m pip check
Copy-Item .env.example .env
```

CUDA PyTorch wheel 应从官方索引安装。当前已验证组合为
`torch 2.13.0+cu130`、CUDA runtime 13.0、Python 3.14；不要在原 `.venv`
中直接替换 PyTorch，以免同时破坏 CPU 回退环境。

编辑 `.env`。如果启用问答生成，需要环境 Key 或已通过设置 API 保存的用户级 DPAPI
Key；如果只使用检索链路，可设 `LLM_ENABLED=false` 并留空 Key。首次启动尚无 DPAPI Key
时，可以先使用环境 Key，或以 retrieval-only 模式启动后再保存 Key：

```dotenv
LLM_ENABLED=true
DEEPSEEK_API_KEY=your_key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
LLM_ANSWER_THINKING_MODE=disabled
RETRIEVAL_TRACE_ENABLED=false
TRACE_STORAGE_MAX_BYTES=52428800
EMBEDDING_DEVICE=cuda:0
EMBEDDING_BATCH_SIZE=32
RERANKER_DEVICE=cuda:0
RERANKER_USE_FP16=true
SPARSE_BACKEND=rank_bm25_memory
SPARSE_INDEX_ROOT=./data/sparse_index
SPARSE_INDEX_CORPUS_ID=
OCR_ENABLED=false
OCR_PDF_SCANNED=false
OCR_PDF_PAGE_IMAGES=false
OCR_EMBEDDED_IMAGES=false
OCR_IMAGE_PREVIEW_ENABLED=true
OCR_IMAGE_DIR=./data/ocr_images
```

`bm25s_mmap`只适用于已用`scripts/rebuild_sparse_index.py`构建并验证的匹配generation；
缺失、dirty、损坏或合同错配不会自动退回全库内存BM25。当前生产默认值不启用该后端。
固定1000篇/120题的P4同期对照显示，mmap的Sparse暖态均值由20.700 ms降至5.630 ms，
完整暖态均值由354.806 ms降至342.947 ms；质量与GPU峰值不变。该结果只进入P5评审，
尚未授权修改默认后端，也不能外推到更大语料或生产容量。完整证据见
`eval/crud_rag_1000/v3_1/bm25_persistence_p4/README.md`。

在设置页的“视觉模型”中开启总开关和所需场景。默认 `VISION_SOURCE=follow_llm`，
使用已保存的问答服务商、地址、模型及密钥；关闭问答生成不会关闭视觉识别。
所选模型须支持图片输入（例如当前部署所使用的 `deepseek-flash`）。选择“自定义模型”后，
可独立配置连接及加密密钥，`VISION_*` 环境变量作为自定义连接的部署默认。
页面密钥优先于环境变量；清除页面密钥恢复环境变量，不会清除另一类模型的密钥。

文字识别和图片描述分别配置思考模式/强度、Temperature、最大输出 Tokens 和超时；
默认关闭思考，Temperature 为 0，输出上限分别为 4000/800，超时 60 秒。
“测试视觉模型”对当前视觉草稿发送一张内置图片，检查实际识别结果，不保存草稿；
跟随模式使用**已保存**的问答连接。测试及正式识别均会消耗服务商额度。
视觉设置从下次文档上传/重新解析生效，每份文档固定使用一份配置；旧文档不会自动更新，
仅重建检索索引也不会补做 OCR。页面不会开放 Prompt、并发或重试策略编辑。

视觉链路排查：先在设置页点击“测试视觉连接”。它验证真实图片输入和识别结果，而不只是接口可达；
跟随模式使用已保存的问答连接，自定义模式使用视觉草稿。再上传一份小文档，检查上传警告及文档预览。
扫描页识别失败会显示“视觉识别未完成”及原因，不再与未启用识别时的“无可提取文本”混为一谈。
`logs/backend.error.log` 中搜索 `Vision failure` 可查页/图片对象、模型、错误类别、HTTP 状态及服务商请求 ID。
HTTP 429 仅确认服务商拒绝了该次请求，是否属于并发、频率或额度限制仍需结合警告里的服务商代码和服务商后台判断。
当前单个后端进程的视觉调用按 1 路执行，正常调用沿用 SDK 最多 2 次重试，草稿测试不重试；
这不能保证避开账号级额度或其他应用同时调用的限制。空回复、输出截断不会作为成功结果写入缓存。
修改模型、任务输出参数或图片内容会使用新缓存；更换密钥或提高超时后，旧的短期失败记忆不会阻止重试。

需要独立复现完整链路时，在项目目录运行：

```powershell
.\.venv-gpu\Scripts\python.exe -m scripts.validate_vision_settings --run-external
```

该命令会真实调用当前已保存的问答模型（可能产生少量费用），只发送程序生成的测试图片和两页扫描式 PDF，
并在 `logs/vision-acceptance-*/` 的隔离库中验收上传、识别、入库、预览，输出 `report.json` 和 `preview.json`。
不会改动生产设置、原文档或生产索引；这是一项链路验证，不是复杂版式的 OCR 准确率评估。
识别结果缓存按有效连接、任务、参数和图片区分；限流、超时、空输出、截断通过上传警告报告。

Embedding 和 Reranker 都以 `local_files_only=True` 加载。运行前需要确保以下模型已在
Hugging Face 本地缓存：

- `BAAI/bge-large-zh-v1.5`；
- 启用 `RERANKER_ENABLED=true` 时需要 `BAAI/bge-reranker-v2-m3`。

BGE large 输出 1024 维向量，旧的 384 维索引不能直接复用。文档结构、Embedding 模型或
chunk 规则变化后，应使用 `scripts/rebuild_index.py` 在独立目录安全重建，不要直接覆盖
现有索引。

## 启动

开发时分别在两个终端启动 FastAPI 与 Vite。后端终端从仓库根目录运行：

```powershell
.\.venv-gpu\Scripts\python.exe -m uvicorn backend.main:app --reload --reload-dir backend
```

前端终端运行：

```powershell
Set-Location web
npm.cmd run dev
```

Vite 默认通过 `http://localhost:5173/` 提供开发页面，并把 `/api` 代理到 8000。
只有需要与旧界面对照时，才从仓库根目录另行运行
`.\.venv-gpu\Scripts\python.exe -m streamlit run frontend/app.py`。

或使用一键脚本：

```powershell
.\scripts\start.ps1
```

默认 profile 为 GPU：使用 `.venv-gpu`，Embedding/Reranker 均为 `cuda:0`，
Reranker 使用 FP16。需要故障回退时才显式启动 CPU profile：

```powershell
.\scripts\start.ps1 -InferenceProfile cpu
```

CPU profile 使用原 `.venv`，并在启动进程中强制覆盖为 CPU/FP32；两个 profile
不会同时启动，防双开检查仍然生效。

只有需要后端热重载时才使用：

```powershell
.\scripts\start.ps1 -Reload
```

一键脚本默认重新构建并打开 React 页面，不再启动旧 Streamlit。只在人工对照旧界面时使用：

```powershell
.\scripts\start.ps1 -LegacyFrontend
```

不希望自动打开浏览器时可附加 `-NoBrowser`。

访问地址：

- React SPA（主入口）：http://localhost:8000/
- API 文档：http://localhost:8000/docs
- 旧 Streamlit（仅 `-LegacyFrontend` 时启动）：http://localhost:8501

`start.ps1` 每次都会重新构建 React SPA，由 FastAPI 静态托管，并将它作为默认打开页面。
启动脚本会等待后台 Embedding/Reranker 预热状态变为 `ready`
后才报告成功；模型加载失败时会停止刚创建的后端进程，并在错误日志中保留完整异常。
脚本使用独立隐藏进程，启动成功后会记录 PID，再次启动前检测到服务仍在运行会拒绝执行
（防双开）。一键停止服务：

```powershell
.\scripts\stop.ps1
# 或等价地：
.\scripts\start.ps1 -Stop
```

`stop.ps1` 会校验记录中的进程启动时间（防止 PID 被复用后误杀），优先用 `taskkill /T /F`
终止整个进程树（覆盖 `-Reload` 模式下 Uvicorn 的子进程）；若隐藏窗口的 `conhost` 令
`taskkill` 失败，则回退终止已验证的主进程，并清理当前启动日志中经过身份校验的 Uvicorn
worker；确认全部退出后才清理 PID 文件。服务异常退出或手动结束后，启动输出中的
Backend/Frontend PID 仍可作为兜底手段：

```powershell
Stop-Process -Id <Backend PID>,<Frontend PID>
```

如果上传中断，查看 `logs/backend.error.log`。失败文件不会因页面刷新而自动重试，需要
在前端显式点击“重试失败文件”。

## 会话中的模型思考

React 会话会单独显示最终回答模型实际返回的 `reasoning_content`：收到思考片段时默认展开，
第一段非空正文到达时自动折叠一次，之后可以手动展开或收起。没有思考文本的响应不显示空面板。
是否返回思考文本取决于上游接口，开启思考开关不保证接口会提供该字段。

成功回答的思考内容随消息保存，重新打开历史会话时默认折叠。metadata schema v5 会为旧消息
补充默认空值，保留现有会话和知识库数据；升级前可备份 `data/metadata.db`。
思考文本不参与正文引用提取，也不加入后续问答历史提示词。查询改写和 OCR 思考不在会话展示范围内。

仅有思考、正文为空仍判定为失败。失败或主动停止时，当前页面保留已接收内容并提示未完成；
刷新后不保留半截内容，与现有失败会话策略一致。
流式协议在 `delta` 之外增加 `reasoning_delta` 事件，其 `text` 只包含思考片段。

## 文档生命周期

每次上传都会创建独立 `document_id`：

```text
临时文件
  → SQLite processing 记录
  → structured-v2 parse（可选 OCR）
  → document_units
  → chunk / embed
  → Chroma
  → 原子移动为 document_id.ext
  → SQLite ready
```

同名文件会成为两个独立版本。失败时会清理临时文件、结构化 unit 和可能写入的向量，
同时保留 `failed` 记录用于排查。删除文档时按 `document_id` 级联清理关联数据。

## 测试

### 本地完整测试

默认测试不会调用 DeepSeek，也不会写入生产 `data/`：

```powershell
.\.venv-gpu\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
```

2026-09-18 完整后端复验结果为 `738 passed, 3 skipped`。跳过项均为需要显式授权才会
执行的真实外部 LLM 测试；普通测试使用隔离临时目录，不写入生产 `data/`。建议明确传入
`tests`，避免 pytest 从仓库根目录递归扫描本地 `logs/` 验收临时目录并因 Windows ACL
产生与代码无关的收集错误。

不依赖本地模型权重的 CI 测试：

```powershell
.\.venv-gpu\Scripts\python.exe -m pytest -m "not integration and not external" -q
```

React 前端检查：

```powershell
Set-Location web
npm.cmd test -- --reporter=dot
npm.cmd run lint
npm.cmd run build
```

2026-09-18 结果为 12 个测试文件、62/62 测试通过，lint、TypeScript 和生产构建通过；
构建仍有单个 JavaScript chunk 超过 500 kB 的体积警告。

### 真实 DeepSeek 测试

`tests/test_llm.py` 包含基于上下文回答、无关问题拒答和流式输出三项真实 API 测试。
当前 `tests/conftest.py` 会为隔离测试覆盖 `DEEPSEEK_API_KEY`；在该问题修复前，可在明确
允许外部调用后，仅对这个测试文件禁用公共 conftest：

```powershell
.\.venv-gpu\Scripts\python.exe -m pytest --noconftest tests/test_llm.py -m external -q -s
```

2026-07-30 使用 `.env` 中配置的 `deepseek-v4-flash` 实测为 `3 passed`。

## Dataset v3

Dataset v3 使用单一顶层 JSON、独立 corpus manifest 和 `structured-v2` parser。当前包括：

- 35 道单题；
- 6 组对话、17 个 dialogue turn；
- 43 个可回答项、9 个不可回答项；
- 80 条确定性定位的 evidence；
- PDF、DOCX、TXT、Markdown、PPTX 共 23 份语料。

非严格校验用于检查结构、manifest 和证据绑定：

```powershell
.\.venv-gpu\Scripts\python.exe -m eval.dataset_v3 validate `
  --dataset eval/dataset/questions_v3.json `
  --corpus eval/corpus `
  --manifest eval/dataset/corpus_v3_manifest.json
```

严格校验只应在真实审核者完成全部签核后通过：

```powershell
.\.venv-gpu\Scripts\python.exe -m eval.dataset_v3 validate `
  --dataset eval/dataset/questions_v3.json `
  --corpus eval/corpus `
  --manifest eval/dataset/corpus_v3_manifest.json `
  --strict
```

当前非严格校验通过；严格校验按设计失败，因为所有审核组仍为 `draft`，不能自动补写为
`verified`。

## v3 完整预检结果

2026-07-30 曾运行完整 structured-v1 预检。以下结果作为升级前历史基线保留，不代表当前
structured-v2 评测结果。该运行创建隔离 Chroma、SQLite 和上传目录，并在用户明确授权后，
将每题检索到的 Top-K 片段发送给 DeepSeek：

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:HF_HUB_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
.\.venv-gpu\Scripts\python.exe -m eval.eval run `
  --label <label> `
  --dataset eval/dataset/questions_v3.json `
  --corpus eval/corpus `
  --manifest eval/dataset/corpus_v3_manifest.json `
  --allow-draft-v3
```

`--allow-draft-v3` 表示结果只能用于 preflight，不能作为正式冻结评测。

运行规模：

| 项目 | 结果 |
|---|---:|
| 文档 | 23 |
| Parsed units | 2,438 |
| Chunks | 2,584 |
| Parser warnings | 0 |
| 评测项 | 52 |
| DeepSeek 调用 | 51 |
| 空检索短路 | 1 |
| 结构化入库耗时 | 703.11s |

自动指标：

| 指标 | 结果 |
|---|---:|
| Hit Rate | 100.00% |
| Recall | 100.00% |
| MRR | 97.67% |
| Keypoint Coverage | 67.67% |
| Strict Accuracy | 53.49% |
| Citation Precision | 97.67% |
| Citation Recall | 100.00% |
| Citation Emission Rate | 100.00% |
| False Refusal Rate | 0.00% |
| Refusal Accuracy | 100.00% |
| 不可回答题伪引用 | 1 |

延迟：

| 阶段 | Mean | P50 | P95 |
|---|---:|---:|---:|
| Retrieval | 5.674s | 5.557s | 7.976s |
| LLM | 1.793s | 1.701s | 2.977s |
| Total | 7.432s | 7.469s | 10.036s |

### 指标解释

本次 20 个自动 Strict Fail 均已逐项进行 AI 语义复核，未发现明显的核心答案遗漏。
主要误差来自当前评分器使用连续字符串匹配，无法稳定处理以下等价表达：

- Markdown 加粗、代码反引号和标点差异；
- `O(nlog₂n)` 与 `O(nlog2n)` 等符号差异；
- “队尾是允许插入的一端”与“在队尾插入”等同义改写；
- 表格答案中冒号、空格或解释性文字造成的短语不连续。

因此 `Keypoint Coverage` 和 `Strict Accuracy` 当前会系统性低估回答质量，不能单独作为
合并或发布阻断指标。该 AI 复核不替代 Dataset v3 要求的真实人工签核。

当前确认的回答或引用问题：

1. `D-001-q0005` 正确拒答，但仍输出了一个来源标记；
2. `D-001-d0001-t1/t2` 各引用了一个额外的非 gold 文档；
3. CPU Reranker 是当前检索延迟的主要来源。

本次本地报告保存在
`eval/runs/codex-v3-full-preflight-20260730-211947/`。`eval/runs/` 默认被 Git 忽略，
避免把 API 原始回答和临时索引误提交到仓库。

## 历史评测证据

旧 Dataset v1/v2 和 P1/P2 实验结果继续保留，用于追溯历史决策，不作为当前
structured-v2 的主验收集，也不应与 Dataset v3 指标直接比较。

| 阶段 | 结论 | 证据目录 |
|---|---|---|
| Baseline | MiniLM、固定滑窗的初始基线 | `eval/runs/baseline/` |
| P1-1 | 选择 BGE large，无 query instruction | `eval/results/p1-1-bge/` |
| P1-2 | 保守文本清洗和 DOCX 表格原序解析 | `eval/results/p1-2-docx-table/` |
| P1-3 | 选择 token-aware recursive 400/80 | `eval/results/p1-3-recursive-chunking/` |
| P1-4 | 选择 cosine distance threshold 0.67 | `eval/results/p1-4-threshold-calibration/` |
| P1-5 | 启用候选数 15 的 BGE Reranker | `eval/results/p1-5-reranker/` |
| P2-1 | Hybrid 未超过 Dense + Reranker，未部署 | `eval/results/p2-1-hybrid-retrieval/` |
| P2-3 | 检索 Trace 完成受控验收 | `eval/results/p2-3-retrieval-trace/` |
| v3 structured preflight | 五格式统一结构和检索预检 | `eval/results/v3-structured-preflight/` |

完整评测命令、指标定义、隔离约束和历史数据集说明见 `eval/README.md`。

## 已知限制与后续工作

当前后续工作只记录未完成的验收或明确问题，不再保留已经完成的迁移计划：

1. 由真实审核者完成 Dataset v3 的问题、答案、证据和不可回答项签核；
2. 改进 keypoint 规范化或引入可靠的语义评分，减少自动误判；
3. 明确拒答时的引用策略，并消除不可回答题伪引用；
4. 检查多轮对话中的额外文档引用；
5. 评估 Reranker 加速方案，降低 CPU 检索延迟；
6. 为 OCR/图片理解建立带人工真值的准确率与版面/表格专项评测；复杂表格目前仍主要以
   OCR 段落保存，不等同于结构化表格恢复；
7. 验证更多 OpenAI-compatible 服务商对 `reasoning_content`、思考开关和思考强度的兼容性；
8. 对 React 主包做按路由或面板拆分，消除单个 JavaScript chunk 超过 500 kB 的警告；
9. 处理 FastAPI lifespan、PyPDF2、Starlette TestClient、`pkg_resources` 等依赖弃用警告；
10. 决定旧 Streamlit 前端的退役时间；如需远程或多人使用，先实现认证、授权和文档级权限。
