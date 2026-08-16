# 阶段 3 前端重构 F0 基线

> 日期：2026-08-15
> 分支：`feature/rag-v2-optimization`
> 基线 HEAD：`47aca19`
> 范围：只记录并补测试，不修改页面生产代码，不安装 Playwright，不进入 F1。

## 1. F0 开始前的工作区

F0 开始时工作区已经存在上一轮未提交改动，不能将它们误认为本阶段新增，也不能清理或覆盖：

```text
M  README.md
M  backend/main.py
M  docs/测试反馈记录.md
M  scripts/start.ps1
M  scripts/stop.ps1
M  web/src/App.tsx
M  web/src/index.css
M  web/src/pages/chat/ChatPage.tsx
M  web/src/pages/knowledge/KnowledgePage.tsx
?? .claude/
?? tests/test_spa_static.py
?? web/src/__tests__/appLayout.test.tsx
?? web/src/pages/chat/chat-page.css
?? web/src/pages/knowledge/knowledge-page.css
```

其中启动/停止脚本、FastAPI 静态托管与 MIME 修复、`.claude/` 均不属于本阶段写入范围。规划文档也是 F0 前已创建的未跟踪文件，本阶段只更新其确认和执行状态。

## 2. 原测试基线

新增页面测试前实际执行：

```text
npm.cmd test -- --reporter=verbose
Test Files  3 passed (3)
Tests       8 passed (8)
```

同一基线下：

- `npm.cmd run lint`：通过；
- `npm.cmd run build`：通过；
- Vite 报告主 JavaScript chunk 约 1,082.65 kB，属于既存性能警告，不是 F0 引入的构建失败。

这 8 个测试只覆盖标签树算法、NDJSON 事件解析和应用布局壳，不能证明页面功能完整。

## 3. F0 新增测试

新增文件：

- `web/src/__tests__/chatPage.test.tsx`；
- `web/src/__tests__/knowledgePage.test.tsx`。

新增的 6 个行为断言：

1. 空标签范围应明确显示“全部知识库”；
2. 选中 `pit` 后聊天请求携带 `tags: ["pit"]`；
3. 清除选择后聊天请求恢复 `tags: []`；
4. 选择文件只暂存，确认后才调用上传 API；
5. 文档标签筛选继续向服务端传递 `tags`；
6. 批量上传失败后保留失败文件，并提供只重试失败项的入口。

## 4. F0 结束时的验证结果

全量执行：

```text
npm.cmd test -- --reporter=dot
Test Files  2 failed | 3 passed (5)
Tests       2 failed | 12 passed (14)
```

12 个通过项包括原有 8 个测试和 4 个新增保护性测试。2 个失败项是预期的 RED 基线：

1. `ChatPage 检索范围 > 未选择标签时明确显示全部知识库，而不是只依赖选择器占位文案`
   - 当前页面找不到“全部知识库”；
   - 对应 F1 的检索范围可发现性修复。
2. `KnowledgePage 关键工作流 > 批量上传失败后保留失败文件，并提供只重试失败项的入口`
   - 当前页面找不到“失败文件保留，可重试”，也没有“重试失败文件”按钮；
   - 对应 F2 的上传队列状态机修复。

其他检查：

- `npm.cmd run lint`：通过；
- `npm.cmd run build`：通过；
- 测试运行期间出现 Ant Design 6 关于 `List`、`Alert.message`、`Space.direction` 的弃用警告，以及 jsdom 不实现伪元素 `getComputedStyle` 的提示；均未导致保护性测试失败，后续重构时应消除生产代码弃用警告。

## 5. 阶段边界

F0 到此完成。当前保留上述 2 个失败测试作为后续实现的验收锁，不在 F0 修改 `ChatPage`、`KnowledgePage` 或 CSS 让它们转绿；收到用户继续指令前不进入 F1。
