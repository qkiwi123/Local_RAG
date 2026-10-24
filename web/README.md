# web — RAG 知识库前端（React SPA）

React + TypeScript + Vite + Ant Design 单页应用，替换原 Streamlit 界面。

## 当前界面与交互

2026-10-24更新：

- 顶部支持日间/夜间切换，首次跟随系统，手动选择后在本机记忆。
- 空白对话的输入区位于中下部，首次发送后移到底部；输入草稿时保持位置。AI回复使用无外框阅读列，正文、来源与输入框统一最大920 px宽度及左右边界。
- 输入框内可搜索、多选资料标签，点击即用于下一次提问；父标签包含子标签，多选取并集，可直接移除或恢复全部资料。
- 上传区域合并文件选择、批次标签和队列，确认/重试/清空操作固定在顶部。批次结束移除成功项，失败项保留，OCR等警告单独展示；清空队列只移除暂存文件。
- 多选/拖拽整批文件只入队一次，校验问题在面板内汇总；设置页默认显示Dense与BM25召回Top K，保存仍使用原有revision及增量PATCH。
- 长引用名称和位置可换行，摘要限制两行，预览按钮受阅读列约束；窄屏按钮放到下一行。

实现说明见[DEV-008](../new_docs/开发/DEV-008-React主前端.md)、[设置说明](../new_docs/开发/DEV-009-运行时设置与密钥.md)，验证范围见[TEST-004](../new_docs/测试/TEST-004-标签与React交互.md)、[TEST-005](../new_docs/测试/TEST-005-设置与Trace回归.md)。自动化中的Mock上传/问答不能代替真实入库或外部模型验收。

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

2026-10-24提交前对最终前端复跑14个测试文件、86项全部通过，lint及TypeScript/生产构建通过；各阶段真实浏览器和Mock证据见[提交前验证记录](../new_docs/测试/TEST-004-标签与React交互.md#frontend-commit)。

## 常用命令

| 命令 | 说明 |
|---|---|
| `npm run dev` | 开发服务器（HMR，/api 代理到后端） |
| `npm run build` | 类型检查 + 生产构建 |
| `npm test` | vitest 组件/单元回归（上传、设置、主题、范围、思考、引用和流式协议） |
| `npm run lint` | oxlint 静态检查 |

## 目录结构

```text
src/
  app/           应用壳、主题与外观偏好
  api/           后端 API 客户端、类型与 NDJSON 流解析器
  components/    结构化渲染、引用列表、Trace 抽屉、标签树选择器
  pages/chat/    对话页（会话、流式问答、标签筛选、引用与 Trace）
  pages/knowledge/ 知识库管理页（上传、标签管理、文档列表与预览）
  pages/settings/ 检索、模型、视觉设置与运行状态
  utils/         纯函数工具（标签树构建等，可单测）
  __tests__/     vitest 单元测试
```
