# 阶段 3：React 前端重写与流式回答实施计划

> 依赖：阶段 1（标签 API）、阶段 2（OCR 与 `UploadResponse.warnings`）。
> 设计依据：`docs/superpowers/specs/2026-08-13-rag-v2-optimization-design.md` 第 6 节。
> 原则：功能对齐清单逐项验收后才移除 Streamlit；后端改动最小；测试先行。

## 1. 目标与边界

- 目标：React + Ant Design SPA 完全替代 Streamlit，覆盖对话、引用预览、Trace、
  上传与标签管理，并新增流式回答；
- 边界：不新增认证/多用户；视觉能力不进入聊天输入（图片问答不在本轮）；不迁移
  历史会话数据格式（继续读现有 API）。

## 2. 任务分解（按顺序执行）

### 2.1 工程脚手架

- 目录：`web/`（Vite + React 18 + TypeScript；Ant Design 5 + Ant Design X；
  react-router；zustand；axios；vitest；ESLint/Prettier）
- 内容：
  1. `npm create vite` 初始化 + 依赖安装（版本锁定到 lockfile）；
  2. Vite dev 代理 `/api` → `http://127.0.0.1:8000`；
  3. 目录结构：`src/api/`（类型与请求函数）、`src/stores/`、`src/pages/chat/`、
     `src/pages/knowledge/`、`src/components/`；
  4. CI 不强制跑前端构建（本地项目），但提供 `npm run build` 与 `npm test` 脚本说明。

### 2.2 后端流式端点

- 文件：`backend/routers/chat.py`（新增）、`backend/services/llm.py`（流式生成复用）
- 内容：`POST /api/chat/stream`，请求体与 `/api/chat` 相同；
  - 事件序列（NDJSON，每行一个事件）：`meta` → `rewrite`（可选）→ `delta`* →
    `citations` → `done`；错误时 `error`（含错误码）并正确执行 `fail_chat`；
  - 复用 `begin_chat` / `complete_chat` / `fail_chat` 与 request_id 幂等重放：
    重放请求直接返回 `meta` + 完整 `delta` + `done`；
  - 空检索短路同样走流式协议（`done` + 固定拒答文案）；
  - 检索失败/LLM 失败映射为明确 `error` 码（`retrieval_error` / `llm_error` /
    `persistence_error`）。
- 失败测试先行：流式事件顺序合法；重放幂等；失败事件与持久化状态一致；并发
  `expected_last_message_id` 冲突返回 409。

### 2.3 API 客户端与类型

- 文件：`web/src/api/*`
- 内容：与后端 Pydantic schema 对齐的 TS 类型（chat/conversations/documents/tags/
  citations/trace）；统一错误映射（HTTP 状态 → 用户文案）；SSE 解析器
  （fetch + ReadableStream，逐行 JSON 解析，可单测）。
- 失败测试先行：SSE 解析器处理分片/粘包/空行/错误帧；错误映射表覆盖。

### 2.4 对话页

- 内容：会话列表（新建/切换/重命名/删除确认）；Ant Design X 气泡流式渲染；失败消息
  重试；标签筛选器（多选树、父级联动、当前筛选徽标）；引用列表 + 预览弹窗（结构化
  渲染、高亮、OCR 徽标、已删除来源提示）；Trace 抽屉（受控开启时）。
- 验收：与 Streamlit 行为逐项对齐（见设计文档 6.3 清单）。

### 2.5 知识库管理页

- 内容：上传（多文件、带标签多选、暂存 + 确认、进度、失败重试、解析/OCR 警告展示）；
  标签树管理（增删改、新建空标签、路径校验、删除确认、文档数展示）；文档列表
  （按标签分组/筛选、删除确认、详情）；分类树与文档双向挂标签；
- 配套后端小改：`UploadResponse` 增加 `warnings` 字段（阶段 2 已预留解析警告来源）。

### 2.5a 文档库预览与列表筛选（2026-08-13 验收反馈确认的需求）

- 后端新端点 `GET /api/documents/id/{document_id}/preview`：
  - 复用 `unit_store.list_document_units` 返回整份文档的结构化 units
    （`StructuredPreviewUnit` 列表 + 文档名/标签/位置摘要）；文档不存在或非 ready 404；
  - 不返回文件系统路径、不加载原文件；结构 JSON 读取时继续过 Pydantic 校验；
  - 测试先行：mock 与真实 DB 两条路径、404、空 units、schema 校验失败拒绝返回；
- 后端 `GET /api/documents` 增加可选 `tags` 查询参数：复用
  `expand_tag_paths`/`resolve_document_ids`（父级含子孙、多选取并集），返回命中文档；
  不含标签时返回全部（向后兼容）；测试覆盖展开语义与并集；
- SPA 文档库：标签树筛选列表（调用带 `tags` 的列表接口）、点击文档打开结构化预览
  （与引用预览共用 block 渲染组件，预留 OCR 徽标）。

### 2.6 部署与启动

- FastAPI 静态托管 `web/dist/`（`StaticFiles` 挂载，SPA fallback 到 index.html）；
- `scripts/start.ps1` 适配：构建前端（或检测 dist 存在）+ 启动后端；开发模式说明
  （前后端分开启动，Vite 代理）；
- 不改变现有 API 路径（`/api/*` 保留）。

### 2.7 功能对齐验收与 Streamlit 移除

- 按设计文档 6.3 功能对齐清单逐项验收（含刷新恢复、重启恢复、删除来源文档后的
  历史引用行为）；
- 验收通过后移除 `frontend/`（Streamlit）及对应测试（`tests/test_frontend_app.py`、
  `tests/test_citation_view.py`、`tests/test_frontend_http_utils.py`），更新
  `README.md` 的启动说明。

## 3. 文件改动清单（预期）

```text
A  web/                       （React SPA，含 package.json、src/**、vitest）
M  backend/routers/chat.py    （新增 /api/chat/stream）
M  backend/routers/upload.py  （新增 /api/documents/id/{id}/preview、列表 tags 查询参数）
M  backend/models/schemas.py  （UploadResponse.warnings、流式/预览相关模型）
M  backend/main.py            （静态托管）
M  scripts/start.ps1
M  .gitignore                 （node_modules、dist）
D  frontend/                  （对齐验收后移除，连同其测试）
M  README.md                  （启动与前端说明，阶段 4 完成）
A  web/src/__tests__/         （api client、SSE 解析、store 单测）
A  tests/test_document_preview.py
```

## 4. 阶段验收标准

1. 流式回答逐字渲染，失败/重试/重放行为与持久化协议一致；
2. 功能对齐清单全过，Streamlit 已移除；
3. 标签筛选、标签管理、上传警告在 SPA 中可用；
4. 文档库可按标签筛选，点击文档可查看结构化预览；
5. 生产构建可由 FastAPI 单服务托管，`scripts/start.ps1` 一键可用；
6. 全量 pytest 绿色（含流式端点与预览端点测试）；vitest 绿色；
7. 提交主题清晰（脚手架 / 流式端点 / 对话页 / 知识库页 / 移除 Streamlit）。

## 5. 风险与备注

- 前端重写范围大：严格按对齐清单推进，Streamlit 过渡保留到验收通过，避免过早移除；
- Ant Design X 版本兼容：锁定版本并在 package.json 记录；
- 流式与持久化的幂等语义复杂：复用既有 conversation_store 协议，不另造状态机；
- 静态托管路径与 API 前缀冲突：SPA fallback 仅作用于非 `/api` 路径，需集成测试；
- `.env`、生产 `data/` 全程不动。
