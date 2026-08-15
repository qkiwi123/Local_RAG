# 阶段 3：React 前端重写与流式回答实施计划

> 依赖：阶段 1（标签 API）、阶段 2（OCR 与 `UploadResponse.warnings` 预留）。
> 设计依据：`docs/superpowers/specs/2026-08-13-rag-v2-optimization-design.md` 第 6 节。
> 2026-08-15 按用户确认增补：后端前置任务组（4 个待实现接口）、过渡期功能
> 完整清单、流式中断采用方案甲（中断即按失败处理，不保存半截答案，可重试）。
> 原则：后端前置接口先行；功能对齐清单逐项验收后才移除 Streamlit；测试先行。

## 1. 目标与边界

- 目标：React + Ant Design SPA 完全替代 Streamlit，覆盖对话、引用预览、Trace、
  上传与标签管理，并新增流式回答（可中断、中断按失败处理并可重试）；
- 边界：不新增认证/多用户；视觉能力不进入聊天输入（图片问答不在本轮）；
  不迁移历史数据（继续读现有 API；无需 schema 变更）。

## 2. 任务分解（按顺序执行）

### 2.1 后端前置（四个接口，全部尚未实现，SPA 之前必须完成）

#### 2.1.1 `POST /api/chat/stream`（SSE 流式 + 中断语义甲）

- 请求体与 `/api/chat` 相同（question/conversation_id/request_id/
  expected_last_message_id/retry_failed/tags）；
- 事件序列（NDJSON，每行一个事件）：
  `meta`（conversation_id/request_id/user_message_id/standalone_question）→
  `delta`*（token 片段）→ `citations` → `done`（含 assistant_message_id）；
  失败时 `error`（错误码）；
- **中断语义甲**：客户端断开（停止按钮/连接中断）时**按失败处理**——
  复用 `fail_chat`（error_code `interrupted`），不保存半截答案；前端显示失败态
  并提供重试（重新生成）；无需新增数据状态；
- 幂等与并发：复用 `begin_chat`/`complete_chat`/`fail_chat` 协议；重放已 complete
  请求直接单帧 `done` 返回；空检索短路走同一流式协议（`delta` 一次 + `done`）。

#### 2.1.2 `GET /api/documents/id/{document_id}/preview`

- 复用 `unit_store.list_document_units` 返回整份文档的结构化 units
  （文档名、标签、unit 列表：location + canonical_text + blocks，blocks 保留
  `attributes` 供 OCR 徽标）；文档不存在或非 ready → 404；
  不返回文件系统路径、不加载原文件；结构 JSON 继续过 Pydantic 校验。

#### 2.1.3 `GET /api/documents?tags=`（文档库服务端筛选）

- 复用 `expand_tag_paths`/`resolve_document_ids`（父级含子孙、多选取并集），
  返回命中文档；不含 tags 参数返回全部（向后兼容）；与问答检索筛选同一套语义。

#### 2.1.4 `UploadResponse.warnings`

- 透出解析与 OCR 警告（`code`/`message`/`location_label` 摘要），供上传界面展示；
- 后端前置组整体验收：4 接口全部有 pytest（含 SSE 解析器、
  中断按失败处理、重放、预览 404、tags 筛选语义、warnings 透出）。

### 2.2 工程脚手架

- 目录 `web/`：Vite + React 18 + TypeScript；Ant Design 5 + Ant Design X；
  react-router；zustand；axios（或 fetch 封装）；vitest；ESLint/Prettier；
- Vite dev 代理 `/api` → `http://127.0.0.1:8000`；
- 目录结构：`src/api/`（类型与请求函数）、`src/stores/`、`src/pages/chat/`、
  `src/pages/knowledge/`、`src/components/`；
- 提供 `npm run build` 与 `npm test` 说明；版本锁定到 lockfile。

### 2.3 API 客户端与类型

- 与后端 Pydantic schema 对齐的 TS 类型（chat/conversations/documents/tags/
  citations/trace/stream 事件）；
- 统一错误映射：HTTP 状态 → 用户文案；**409 会话冲突自动重同步**（拉取当前
  会话并提示「会话已在服务端更新，请重新发送」）；
- NDJSON 流解析器（fetch + ReadableStream，处理分片/粘包/空行/错误帧），可单测。

### 2.4 对话页

- 会话列表：新建/切换/重命名/删除（**删除带确认**）；
- 提问：Ant Design X 气泡；**流式逐字渲染**；**「停止生成」按钮 → 中断语义甲**
  （按失败处理：显示失败态并可直接重试）；失败消息重试；
- 标签筛选：多选树（父级联动子级）、当前筛选徽标、一键清除；与文档库筛选
  共用同一套标签语义；
- 引用：来源列表、预览弹窗（结构化渲染：标题/段落/列表/表格/代码、高亮、
  **OCR 徽标**「AI 识别，可能存在误差」）、已删除来源提示；
- Trace 抽屉：阶段表、候选行选择、完整 chunk、耗时条（含「其他阶段」说明，
  见反馈 #11）；**trace 持久化行为**：跨会话/刷新/重启可查，trace 为 null 或
  被 LRU 淘汰时不渲染面板；
- 空结果文案区分全库/标签筛选（后端已实现，前端原样展示）。

### 2.5 知识库管理页

- 上传：**三段式暂存 + 确认**（暂存列表、空暂存提示、后端就绪检测、失败重试）、
  多文件、**上传时新建标签并自动选中**、进度、**解析/OCR 警告展示**
  （`UploadResponse.warnings`）；
- 标签管理：树形展示（父子层级、**双层计数「本层 N · 含子级 M」**）、
  新建（父级自动创建说明）、重命名（子树联动提示）、删除（**确认提示**：
  「文档保留，仅解除关联」）；
- 文档列表：**详情字段**（大小/上传时间/chunk 数/标签）、**按标签筛选**
  （服务端 tags 参数，与检索同一套语义）、**删除带确认**、
  **点击预览**（结构化整文档预览，按 unit 顺序渲染位置标签，无高亮浏览模式）、
  文档「🏷 编辑标签」入口（多选 + 新标签输入 + 保存）。

### 2.6 部署与启动

- FastAPI 静态托管 `web/dist/`（SPA fallback 仅作用于非 `/api` 路径）；
- `scripts/start.ps1` 适配：构建前端（或检测 dist）+ 启动后端；
- 开发模式说明（前后端分开启动，Vite 代理）；不改变现有 API 路径。

### 2.7 功能对齐验收与 Streamlit 移除

- 按总设计 6.4 对齐清单逐项验收（含刷新恢复、重启恢复、删除来源文档后的
  历史引用行为、trace 跨会话查看、流式中断语义乙）；
- 验收通过后移除 `frontend/`（Streamlit）及对应测试（`tests/test_frontend_app.py`、
  `tests/test_citation_view.py`、`tests/test_frontend_http_utils.py`），更新
  `README.md` 的启动说明。

## 3. 文件改动清单（预期）

```text
A  web/                       （React SPA，含 package.json、src/**、vitest）
M  backend/routers/chat.py    （新增 /api/chat/stream）
M  backend/routers/upload.py  （preview 端点、列表 tags 查询参数、warnings）
M  backend/models/schemas.py  （流式/预览/warnings 模型）
M  backend/main.py            （静态托管）
M  scripts/start.ps1
M  .gitignore                 （node_modules、dist）
M  .env.example               （无新配置，如有流式超时项则补充）
D  frontend/                  （对齐验收后移除，连同其测试）
M  README.md                  （启动与前端说明，阶段 4 完成）
A  tests/test_chat_stream.py
A  tests/test_document_preview.py
A  web/src/__tests__/         （api client、SSE 解析、store 单测）
```

## 4. 阶段验收标准

1. 流式回答逐字渲染；**中断按失败处理（不保存半截），前端可重试**；
   失败/重试/重放行为与持久化协议一致；
2. 四个后端前置接口全部测试通过；
3. 功能对齐清单（总设计 6.4）全过，Streamlit 已移除；
4. 标签筛选、标签管理、上传警告、文档库预览与筛选在 SPA 中可用；
5. 生产构建可由 FastAPI 单服务托管，`scripts/start.ps1` 一键可用；
6. 全量 pytest 绿色；vitest 绿色；
7. 提交主题清晰（后端前置 / 脚手架 / 对话页 / 知识库页 / 移除 Streamlit）。

## 5. 风险与备注

- 流式中断检测（GeneratorExit/连接断开）在 ASGI 各服务器上行为差异：
  以 uvicorn 实测为准；中断走 `fail_chat` 复用既有失败语义，幂等可重试；
- 前端重写范围大：严格按对齐清单推进，Streamlit 过渡保留到验收通过；
- Ant Design X 版本兼容：锁定版本并在 package.json 记录；
- 静态托管路径与 API 前缀冲突：SPA fallback 仅作用于非 `/api` 路径，需集成测试；
- `.env` 只做逐键写入/追加，禁止整文件重写（见测试反馈 #18）；
- 生产 `data/` 全程不动。
