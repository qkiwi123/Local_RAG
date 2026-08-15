# RAG v2 优化整体设计

> 状态：已由用户于 2026-08-13 确认（含数据策略、前端技术栈与范围决策）。
> 基线：`master` @ 57bb273。实施分支：`feature/rag-v2-optimization`（待创建）。
> 本设计在「structured-v1」完整链路（五格式解析、结构化预览、持久化多轮对话、
> Dataset v3 框架）之上做三部分增量优化，不重写现有能力，不迁移向量数据库。

## 1. 背景与目标

当前系统已经具备稳定主链路，但存在三类明确缺口：

1. 扫描件、纯图片 PDF 页、文档内嵌图片与图表完全不可检索，只能输出
   `[第X页无可提取文本]` 占位文本；
2. 检索永远面向全库，文档多了以后候选空间、开销和噪声同步放大，无法按
   业务目录缩小范围；
3. 前端是 Streamlit 单文件应用，做目录树管理、流式渲染与复杂交互越来越吃力，
   也不符合主流前端技术栈的长期维护要求。

本轮目标：

- 用多模态模型 API 补上 OCR 与图片理解，且不改变现有问答链路；
- 用多级树形标签实现按目录筛选检索，缩小范围、降低开销、提高准确率；
- 用 React + Ant Design 重写前端，补齐流式回答与知识库管理界面；
- 所有质量结论以评测证据为准，不宣称未经评测的收益。

## 2. 决策记录

| # | 决策点 | 结论 |
|---|---|---|
| 1 | 视觉模型 | 智谱 **GLM-4.6V-Flash**（OpenAI 兼容，官方免费档 9B 轻量版；2026-08-14 用户确认替换原 MiMo 选型）；密钥与开关全部走 `.env`，代码默认关闭 |
| 2 | 问答模型 | **DeepSeek 链路不动**；仅新增独立 `VISION_*` 配置与 `OCR_ENABLED` 开关，关闭时视觉模块完全不加载，系统行为与现状一致 |
| 3 | OCR 范围 | A + B + C 全量（扫描页、PDF 页内图表、DOCX/PPTX 内嵌图片），三层独立开关默认关；MD 外部相对路径图片不支持 |
| 4 | 分类形态 | **多级树形标签 + 多标签**（文档↔标签多对多，任意层级，父级筛选自动含子孙，多选取并集） |
| 5 | 前端技术栈 | **React + Vite + TypeScript + Ant Design（含 Ant Design X）**，独立 SPA；FastAPI 后端不动 |
| 6 | Streamlit 去留 | 过渡保留，新前端功能对齐验收后移除 |
| 7 | 流式回答 | 纳入（后端流式端点 + 前端增量渲染） |
| 8 | 生产数据 | 接受 `structured-v2` 隔离验收后一次性重建 |
| 9 | Trace 持久化 | 2026-08-14 用户确认：Trace 持久化到 SQLite（`message_traces` 表，schema v3），总量超过 `TRACE_STORAGE_MAX_BYTES`（默认 50MB）按创建时间淘汰最旧记录（LRU）；绑定助手消息，删除会话时级联清理；复用 `RETRIEVAL_TRACE_ENABLED` 同一开关（关了不计算也不存储）；重放恢复已存 Trace |
| 10 | 模型预热 | 2026-08-14 用户确认：服务启动后后台预热 Embedding/Reranker（`PREWARM_MODELS=true`），把首次查询的模型加载耗时挪到启动期 |
| 11 | 流式中断语义 | 2026-08-15 用户确认**方案甲**：流式生成被中断时按失败处理（复用 `fail_chat`，不保存半截答案），用户可重试重新生成；无需新增数据状态 |
| 12 | 阶段 3 清单增补 | 2026-08-15 用户确认：A 类（4 个后端前置接口独立任务组）、B 类（过渡期功能：409 自动重同步、上传三段式、标签管理完整形态、筛选语义统一、🏷 编辑、Trace 持久化行为）、C 类（OCR 徽标来源、删除确认统一、文档详情字段、预览边界、测试项）全部采纳 |

## 3. 数据契约版本与数据策略

### 3.1 版本升级记录

1. **`PARSER_VERSION`：`structured-v1` → `structured-v2`**（`backend/services/parsers/constants.py`）。
   OCR 文本与图表说明会进入 canonical text，改变 offset 与 chunk。按项目既定规则
   「canonical text 语义变化必须升版本并重建索引」，版本升级是全局的：不启用 OCR 的
   文档输出与 v1 一致，但不会出现「部分文档 v1、部分文档 v2」的混写。
2. **`SCHEMA_VERSION` 演进**（`backend/services/metadata_schema.py`），业务数据全程保留：
   - v1→v2：新增 `tags` / `document_tags` 表（多级树形标签）；
   - v2→v3：新增 `message_traces` 表（检索 Trace 持久化 + LRU 总量限制）。

### 3.2 数据策略：派生数据重建，业务数据迁移

项目对两类数据执行两种策略，本次升级沿用并显式记录该原则：

| 数据类型 | 例子 | 升级方式 | 原因 |
|---|---|---|---|
| 派生数据 | 解析产物（document_units、chunks、向量索引） | 清空重建，永远从源文件重算，不写数据转换迁移 | 源文件在，重算比转换安全、可复现 |
| 业务数据 | 文档登记、会话、消息 | 原地迁移（schema v1→v2 显式迁移语句） | 无法从任何源重建 |

兼容双轨（v1 可读 + v2 新增）不在考虑范围内：

- parser 语义、预览校验、chunker、eval 都要维护两套分支，测试矩阵翻倍；
- 旧 v1 数据本来就没有 OCR（正是本轮目标），保留读取没有业务收益；
- 总设计文档 7.3/7.4 节已明确排除 parser version 双读与旧 offset 猜测。

### 3.3 版本字段的职责（为何不保留旧数据仍需要版本号）

版本字段的职责不是「读取旧数据」，而是：

1. **防错配守卫**：新代码遇到残留旧索引（.env 指错路径、恢复旧备份、代码回退）时响亮
   报错「解析版本不匹配，请清空重建」，而不是静默用错误 offset 切文本、展示错位高亮；
2. **eval 锚定**：评测 manifest 钉死 `parser_version`，保证 gold evidence 的 offset/unit
   不随解析器升级悄悄漂移；
3. **可追溯**：能区分产物由哪一代规则生成；
4. **机械隔离**：`document_units` 主键 `(document_id, parser_version, unit_id)` 使新旧
   版本行在过渡窗口内不互相污染，清理可确定性执行。

### 3.4 生产数据切换路径

OCR 验收通过后执行 `structured-v2` 切换，二选一：

- **路径 A（推荐）**：保留 `data/uploads/` 原始文件 → 用 `scripts/rebuild_index.py`
  （适配 v2 后）在新目录重建 Chroma，SQLite 走 schema v2 迁移保留会话与登记，
  用户无需手动重新上传文档；
- **路径 B**：彻底清空 `data/` → 用户重新上传全部文档并重新打标签（会话丢失）。

无论哪条路径，都必须先经过隔离环境完整验收，再按既有 7.4 节安全流程（停服、只读清单
核对、逐项删除、重启冒烟）执行，且每次操作前向用户展示最终清单并获得确认。

## 4. 第一部分：OCR / 多模态识别（API 接入）

### 4.1 视觉客户端

- 新模块 `backend/services/vision.py`：独立 OpenAI 兼容客户端，配置
  `VISION_API_KEY` / `VISION_BASE_URL` / `VISION_MODEL`（默认指向 MiMo `mimo-v2.5`，
  以平台文档给出的实际值填入 `.env`）；
- 接口：`recognize_text(image_bytes) -> str`（转录文字）与
  `describe_image(image_bytes, context) -> str`（描述图表）；
- Prompt 固定：「转录图片中的全部文字，保留段落与换行，不总结不翻译」/
  「仅描述图片或图表的内容与要点，不要重复页面正文」；
- 设计为可注入/可 mock，单测不发起真实网络请求；与 `llm.py` 的 DeepSeek 客户端完全解耦。

### 4.2 三层识别能力（`.env` 独立开关，全部默认关闭）

| 层 | 内容 | 技术路径 | 产出 |
|---|---|---|---|
| A | 扫描 PDF 无文本页 | `extract_text()` 为空 → `pypdfium2` 渲染页（BSD/Apache 许可，纯 pip，无 poppler）→ Pillow 缩放长边 ≤1600px → `recognize_text` | 识别文本以 `paragraph` blocks 注入 |
| B | 有文本但含图片/图表的 PDF 页 | 整页渲染 → `describe_image` | 图表说明 blocks（`ocr=True`） |
| C | DOCX/PPTX 内嵌图片 | `python-docx` 关系部件 / `python-pptx` `shape.image.blob` 提取原图 → 识别后按原阅读顺序原位插入 blocks | 图片文字/说明 blocks（`ocr=True`） |

MD 图片仅保留 alt 文本的现状不变；data-URI 内嵌图片属于 C 层可选支持，外部相对路径
图片因上传为单文件模式不支持（如需支持需引入 zip 打包上传，另行立项）。

### 4.3 标记、缓存与成本控制

- 所有视觉产出 blocks 写入 `attributes={"ocr": True, "vision_model": "<模型名>"}`，
  结构化预览显示「AI 识别，可能存在误差」徽标，不伪装为原生文本；
- 页/图级缓存 `data/vision_cache/`，键 = 文件 sha256 + 页/图标识 + 模型版本；
  重建索引不重复计费；
- 每文档 OCR 页数/图片数上限、串行调用 + 超时重试、日志不记录图片内容；
- OCR 失败或关闭时保留 `[第X页无可提取文本]` 占位并追加解析 warning
  （复用 `ParsedDocument.warnings`），**入库不失败**。

### 4.4 版本与 eval 联动

- `PARSER_VERSION` 升为 `structured-v2`；
- `scripts/rebuild_index.py` 中写死 `structured-v1` 的文案与 chunk_id 校验规则同步适配，
  相关测试更新；`--accept-chunk-count-change` 继续用于 OCR 引起的 chunk 数变化；
- eval/dataset v3 的 `parser_version` 同步为 `structured-v2`，重跑 `dataset prepare`
  刷新 hash；无 OCR 页文档 canonical text 不变，evidence/offset 不受影响（必须验证）；
- 主问答模型不变，DeepSeek 评测基线继续有效，无需重跑模型切换对比。

## 5. 第二部分：多级树形标签与筛选检索

### 5.1 数据模型（SQLite schema v2）

```sql
CREATE TABLE tags (
    tag_path TEXT PRIMARY KEY,   -- 如 '课程课件/计算机基础/数据结构'
    name TEXT NOT NULL,          -- 末段名称，如 '数据结构'
    created_at TEXT NOT NULL
);

CREATE TABLE document_tags (
    document_id TEXT NOT NULL,
    tag_path TEXT NOT NULL,
    PRIMARY KEY (document_id, tag_path),
    FOREIGN KEY (document_id) REFERENCES documents(document_id) ON DELETE CASCADE,
    FOREIGN KEY (tag_path) REFERENCES tags(tag_path) ON DELETE CASCADE
)
```

- 标签层级由 `tag_path` 前缀推导，**任意深度**（单层为特例）；
- `documents` 表本身不变；标签重命名 = 批量更新 `document_tags.tag_path` 与
  `tags.tag_path`，删除标签 = 级联清关联；
- 路径格式校验：段非空、无首尾斜杠、深度与总长上限、确定性规范化。

### 5.2 检索过滤（关键设计：不修改 Chroma chunk metadata）

- 服务端把「选中标签（含子孙前缀匹配）命中的文档」换算为 `document_id` 集合，
  多标签自然取并集；
- Dense：Chroma `where={"document_id": {"$in": [...]}}`；
- BM25：建索引前按该集合过滤 chunk，索引缓存键加入排序后的标签集；
- Hybrid：两路同过滤后再 RRF，Reranker 不变；
- **存量 chunk 无需 backfill、零迁移成本**；`document_id` 自 structured-v1 起就在
  chunk metadata 中；
- Trace 记录本次生效的标签过滤集合；
- 空结果文案区分「全库无相关文档」与「当前筛选范围内无相关文档」。

### 5.3 API

- `GET /api/tags`：返回标签树 + 每标签文档数；
- `PUT /api/documents/{id}/tags`：整体替换文档标签集；
- `POST /api/upload`：表单增加可选 `tags` 字段；
- `GET /api/documents`：返回项增加 `tags`；支持可选 `tags` 查询参数（与检索筛选复用
  `expand_tag_paths`/`resolve_document_ids` 的「父级含子孙、多选取并集」语义，阶段 3
  随文档库筛选实现）；
- `ChatRequest`：增加可选 `tags: list[str]`，缺省 = 全库检索（完全向后兼容）；
- 标签重命名/删除端点（含级联与确认语义）。

### 5.4 前端与过渡件

**过渡件（Streamlit，2026-08-13 验收反馈确认的形态）**：

- 上传采用**三段式暂存 + 确认**：选择文件仅暂存（不自动入库）→ 选择/新建标签 →
  点击「确认上传」才提交；失败文件保留在暂存区可重试；
- 侧栏独立「标签管理」区：输入路径 + 按钮**新建空标签**（`POST /api/tags`），并展示
  现有标签与文档数；
- 上传区同样提供新建标签按钮，**创建成功后自动选入本次上传的标签**；
- 已上传文档列表提供「编辑标签」入口（复用 `PUT /api/documents/id/{id}/tags`）；
- 以上部件均为临时件，随 Streamlit 一起移除。

**新 SPA（阶段 3）**：

- 知识库管理页：标签树（增删改、文档挂标签）、上传（带标签与确认）、文档列表/删除、
  解析与 OCR 警告展示；
- 文档库：列表按标签筛选（服务端 `tags` 查询参数，与检索筛选同一套展开/并集语义）、
  点击文档打开**结构化内容预览**（新端点 `GET /api/documents/id/{document_id}/preview`
  返回整份文档的结构化 units，与引用预览共用渲染组件）；
- 对话页：标签筛选器（多选、父级联动子级、当前筛选徽标）。

## 6. 第三部分：前端重写与流式回答

### 6.1 技术栈与结构

- React + Vite + TypeScript + Ant Design（含 Ant Design X）+ react-router + zustand +
  axios/fetch；vitest 单测；oxlint；实际锁定版本以 `web/package.json` 为准
  （脚手架安装为 React 19 / AntD 6 / @ant-design/x 2.9 / react-router 7，功能已验证）；
- 目录 `web/`，两个页面：
  - 「💬 对话」：会话列表、聊天流、引用预览弹窗（含 OCR 徽标）、检索 Trace 抽屉、
    失败重试、标签筛选器；
  - 「📚 知识库管理」：上传（带标签、暂存 + 确认）、标签树管理（含新建空标签）、
    文档列表/删除、**按标签筛选文档、点击文档打开结构化预览**、解析与 OCR 警告展示；
- dev 模式 Vite 代理 `/api` → `127.0.0.1:8000`；生产构建产物由 FastAPI 静态托管，
  `scripts/start.ps1` 适配。

### 6.2 文档库预览与筛选（后端）

- 新端点 `GET /api/documents/id/{document_id}/preview`：复用
  `unit_store.list_document_units` 返回整份文档的结构化 units
  （`StructuredPreviewUnit` 列表 + 文档名/标签/locator 摘要；blocks 保留
  `attributes` 供 OCR 徽标），渲染与引用预览共用组件（标题/段落/列表/表格/代码块，
  安全转义）；文档不存在或非 ready 返回 404；不返回文件系统路径、不加载原文件；
- `GET /api/documents` 增加可选 `tags` 查询参数：服务端复用
  `expand_tag_paths`/`resolve_document_ids`（父级含子孙、多选取并集），返回命中文档
  （不含任何标签时返回全部）；文档库筛选与问答检索筛选共享同一套标签语义，不写
  第二套逻辑。

### 6.3 流式回答（含中断语义甲）

- 后端新增 `POST /api/chat/stream`（POST + fetch 流式读取；事件序列：
  `meta`（conversation_id / request_id / user_message_id / standalone_question）→
  `delta`*（token 片段）→ `citations` → `done`（assistant_message_id）/
  `error`（错误码））；
- 复用现有 `conversation_store` 的 begin/complete/fail、request_id 幂等重放、
  `expected_last_message_id` 并发守卫；重放已 complete 请求以单帧 `done` 返回；
- **中断语义甲**（用户 2026-08-15 确认）：客户端断开（「停止生成」按钮或连接中断）时
  **按失败处理**——复用 `fail_chat`（error_code 如 `interrupted`），不保存半截答案；
  前端显示失败态并提供重试（重新生成）；
- 前端 Ant Design X 气泡逐字渲染 + 「停止生成」按钮，完成后就地渲染引用与 Trace。

### 6.4 功能对齐清单（Streamlit 移除的验收门槛）

新 SPA 必须逐项对齐并通过后才移除 `frontend/`（Streamlit）：

1. 会话：新建/切换/重命名/删除（含确认）、刷新恢复、**409 会话冲突自动重同步并提示重发**；
2. 问答：提问、**流式逐字输出 + 停止生成（中断语义甲：按失败处理、可重试）**、
   失败重试、空检索提示（区分全库/标签筛选）、标签筛选（多选树、父级联动子级、
   筛选徽标 + 一键清除）；
3. 引用：来源列表、预览弹窗、结构化渲染（标题/段落/列表/表格/代码）、高亮、
   **OCR 徽标**（`attributes.ocr=True` → 「AI 识别，可能存在误差」）、已删除来源提示；
4. Trace：受控开启时的检索链路面板（阶段表、候选行、完整 chunk、耗时条含
   「其他阶段」口径说明）；**持久化行为**：跨会话/刷新/重启可查，null 或被 LRU
   淘汰时不渲染；
5. 文档：上传（**暂存 + 确认**、带标签、上传时新建标签并自动选中、进度、失败重试、
   **解析/OCR 警告展示**）、列表（**详情字段：大小/上传时间/chunk 数/标签**、
   **按标签筛选**、**删除确认**）、**点击预览整份文档**（按 unit 顺序、位置标签、
   无高亮浏览模式）、文档「🏷 编辑标签」入口；
6. 标签：新建空标签、**树形管理（双层计数「本层 N · 含子级 M」）**、重命名
   （子树联动提示）、删除（确认：「文档保留，仅解除关联」）、文档挂/改标签。

### 6.5 配套后端小改

- `UploadResponse` 增加 `warnings` 字段（透出解析与 OCR 警告）；
- 静态托管与启动脚本适配；不新增认证（继续本地单用户定位）。

## 7. 实施阶段与顺序

| 阶段 | 内容 | 独立验收 | 依赖 |
|---|---|---|---|
| 0 准备 | 本设计与阶段计划落盘；创建 `feature/rag-v2-optimization`；全量测试基线存档 | 基线可复现 | — |
| 1 标签体系 | schema v2 迁移、标签 CRUD API、三模式检索过滤、上传带标签、Streamlit 过渡件（2026-08-13 验收反馈修正：暂存+确认上传、独立新建空标签、已上传文档改标签） | 标签筛选端到端；过渡件交互验收；全库检索回归不劣化 | 阶段 0 |
| 2 OCR/多模态 | `vision.py`（MiMo）、A/B/C 三层、`structured-v2`、rebuild 工具适配、隔离验收、生产重建 | 真实扫描件可检索可引用；OCR 关闭时与 v1 行为一致 | 阶段 1 |
| 3 前端重写 + 流式 | **后端前置**（4 接口：stream/preview/tags 筛选/warnings）→ SPA 骨架 → 对话页 → 知识库管理页 → 功能对齐 | 对齐清单全过，Streamlit 移除 | 阶段 1/2 API |
| 4 回归与文档 | 全量 pytest、eval v3 preflight、legacy 回归、README 更新 | 全部证据存档 | 全部 |

顺序理由：标签改动面最小、无新外部依赖与费用、收益直接（缩小检索范围即降低开销与噪声），
先做可最快拿到可用增量；OCR 引入新供应商、费用、渲染依赖与生产重建，风险最高，放在标签
稳定之后；前端依赖前两者 API 形态，最后做避免返工。

## 8. 测试策略

- **标签**：v1→v2 迁移幂等（存量 v1 库与空库两条路径）、标签 CRUD/级联/重命名、
  子孙前缀展开、Dense/BM25/Hybrid 三模式过滤、并集语义、路径校验、空结果文案、并发；
- **OCR**：mock vision 客户端（触发/回退/缓存命中/数量上限）、图片型 PDF 二进制 fixture、
  DOCX/PPTX 含图 fixture、warning 与强不变量（chunk 切片一致性）、OCR 关闭时输出与
  v1 完全一致；
- **前端**：api client / store / SSE 事件解析 vitest 单测；UI 冒烟清单人工验收；
- **回归**：全量 pytest、CI 子集、eval v3 preflight（draft）、legacy v2、真实
  DeepSeek 与 MiMo 外部测试。

## 9. 风险与应对

| 风险 | 应对 |
|---|---|
| MiMo 调用成本/延迟不可控 | 默认关闭、页/图级缓存、尺寸压缩、数量上限；收益以评测数据为准 |
| OCR 文本有噪声 | `ocr` 标记 + UI 徽标 + 可关；失败不阻塞入库 |
| structured-v2 重建风险 | 沿用 7.4 安全流程：隔离验收 → 清单核对 → 用户确认 → 一次性切换 |
| eval v3 parser_version 漂移 | 同步版本 + `dataset prepare` 重算 hash，并验证 evidence 不变 |
| 前端重写范围大 | 对齐清单逐项验收，Streamlit 过渡保留到验收通过 |
| 标签过滤 `$in` 集合过大 | 本地知识库规模可控；后续超阈值再评估分片方案 |
| 全库固定候选窗口漏召回 | 实测边缘相关文档（距离 0.60、全库排名 21）会因候选窗口截断被漏掉；已按用户决策（方案 C）记为已知限制，引导用标签缩小范围；阶段 3 后可评估扩大候选或两阶段检索 |
| schema v2 迁移改变「拒绝迁移」既有表述 | 仅限版本化库的显式列/表迁移；未版本化旧库仍拒绝；迁移有幂等测试 |

## 10. 成功标准

- 扫描件、PDF 页内图表、DOCX/PPTX 内嵌图片可被检索并定位到正确来源，预览带 OCR 徽标；
- 标签树多级、多标签，父级筛选含子孙、多选取并集，全库检索（不带标签）行为不变；
- 新 React 前端通过功能对齐清单，流式回答可用，Streamlit 已移除；
- 生产数据为纯 `structured-v2`，schema 为当前版本（v2 标签 / v3 Trace），切换过程证据存档；
- 全量测试、真实文档验收、评测回归均有保存证据；
- 不宣称未经评测证明的质量提升。

## 11. 范围边界（本轮不包含）

- 用户账号、权限与多租户；
- Excel、旧 `.ppt/.xls` 格式；
- MD 外部相对路径图片（需 zip 上传，另行立项）；
- 图片问答（聊天框直接传图提问）——视觉能力本轮只服务入库识别，不服务对话输入；
- 自动打标签的 LLM 推荐（可作后续增强）；
- 向量数据库迁移与任何旧解析产物迁移。

## 12. 文档索引与 Git 纪律

- 本设计：`docs/superpowers/specs/2026-08-13-rag-v2-optimization-design.md`
- 阶段计划：
  - `docs/superpowers/plans/2026-08-13-v2-phase-1-tags.md`
  - `docs/superpowers/plans/2026-08-13-v2-phase-2-ocr-vision.md`
  - `docs/superpowers/plans/2026-08-13-v2-phase-3-frontend-streaming.md`
- 阶段 4 结束后更新 `README.md`。

Git 纪律沿用交接指南：写文档不自动提交，先展示 `git status` / `diff --stat`，用户确认后
再提交；一次提交一个主题；不修改 `.env`、`.claude/`；不自动合并 `master`、不自动推送；
生产数据删除必须逐项清单确认。
