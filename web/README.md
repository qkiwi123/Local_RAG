# web — RAG 知识库前端（React SPA）

React + TypeScript + Vite + Ant Design 单页应用，替换原 Streamlit 界面。

## 开发模式

```powershell
cd web
npm install
npm run dev          # http://localhost:5173（/api 由 Vite 代理到 127.0.0.1:8000）
```

需要后端已启动（`..\scripts\start.ps1` 或 `uvicorn backend.main:app`）。

## 生产模式

```powershell
npm run build        # 产物输出到 web/dist
..\scripts\start.ps1 # 后端静态托管 dist（/ 与 /knowledge 均 fallback 到 index.html）
```

## 常用命令

| 命令 | 说明 |
|---|---|
| `npm run dev` | 开发服务器（HMR，/api 代理到后端） |
| `npm run build` | 类型检查 + 生产构建 |
| `npm test` | vitest 单元测试（NDJSON 解析、标签树构建） |
| `npm run lint` | oxlint 静态检查 |

## 目录结构

```text
src/
  api/           后端 API 客户端、类型与 NDJSON 流解析器
  components/    结构化渲染、引用列表、Trace 抽屉、标签树选择器
  pages/chat/    对话页（会话、流式问答、标签筛选、引用与 Trace）
  pages/knowledge/ 知识库管理页（上传、标签管理、文档列表与预览）
  utils/         纯函数工具（标签树构建等，可单测）
  __tests__/     vitest 单元测试
```
