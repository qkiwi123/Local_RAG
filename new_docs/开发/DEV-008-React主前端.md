# DEV-008 React主前端

## 文档信息

- 文档ID：DEV-008
- 文档类型：开发
- 状态：已完成
- 创建日期：2026-08-27
- 最后更新：2026-08-27
- 证据口径：当前文件静态核对；历史验证与待验证事项在正文区分，本次未重跑产品测试。
- 导航：[本类目录](./README.md) · [文档中心](../README.md)

## 1. 目标、来源与模块边界

关系：实现[React工作区](../设计/DES-005-标签与React交互.md#interaction)和[流式客户端](../设计/DES-004-会话流式协议与来源预览.md#stream)。负责用户交互，不自行访问SQLite、Chroma或外部模型。

## 2. 代码地图

| 入口 | 职责 |
|---|---|
| [AppShell.tsx](../../web/src/app/AppShell.tsx) / [App.tsx](../../web/src/App.tsx) | 导航与路由 |
| [useChatController.ts](../../web/src/pages/chat/useChatController.ts) | 消息、发送、停止、重试和409重同步 |
| [ChatPage.tsx](../../web/src/pages/chat/ChatPage.tsx) / [RetrievalScopeBar.tsx](../../web/src/pages/chat/RetrievalScopeBar.tsx) | 会话布局与范围 |
| [KnowledgePage.tsx](../../web/src/pages/knowledge/KnowledgePage.tsx) / [useUploadQueue.ts](../../web/src/pages/knowledge/useUploadQueue.ts) | 文档与逐文件队列 |
| [ResizableDrawer.tsx](../../web/src/components/ResizableDrawer.tsx) / [usePanelResize.ts](../../web/src/components/usePanelResize.ts) | 可调任务面板 |
| [api/stream.ts](../../web/src/api/stream.ts) | NDJSON分块解析 |

<a id="implementation"></a>
## 3. 开发逻辑与实际行为

聊天提交后立即显示待发送用户消息，再显示思考/流式助手状态；完成后服务端消息替换本地临时状态。失败重试复用request_id并设置retry_failed；409先同步会话，不自动重发问题。

知识库默认全宽文档工作区；上传和标签抽屉按需打开；队列关闭后保留。上传只重试失败项，OCR warning作为AI识别风险显示。检索范围位于输入框上方，下一请求带tags。

桌面支持会话栏和抽屉调宽，窄屏使用卡片、移动会话抽屉或全宽任务面板。UI尺寸偏好与后端运行设置分开。

## 4. 实际结果与验证

当前代码与历史F1—F4/S5/S6记录相符；本次没有浏览器验收或前端构建。历史39项前端测试的来源在[S6记录](../测试/TEST-005-设置与Trace回归.md#evidence)；本模块用例与视口矩阵见[TEST-004 标签与React交互](../测试/TEST-004-标签与React交互.md)。

## 5. Bug修复记录

反馈21的布局重叠并非首次报告“已修复”时就被验证；后续重构和多视口验收才形成证据。反馈22/23/24分别对应即时消息、面板调宽、输入区范围位置，详见[历史反馈](../测试/TEST-007-缺陷与历史反馈追踪.md#feedback)及本节代码地图。

## 6. 已知限制与后续

构建仍有较大单chunk警告，暂无本次拆包前后数据；jsdom不替代真实布局。HTTP 200不足以证明React挂载，MIME故障由[托管修复](../开发/DEV-012-服务托管与启动停止.md#bug-fixes)处理。旧frontend/仍保留，未授权删除。

## 关联文档

- 关系：来源设计；[DES-004 会话流式协议与来源预览](../设计/DES-004-会话流式协议与来源预览.md)
- 关系：来源设计；[DES-005 标签与React交互](../设计/DES-005-标签与React交互.md)
- 关系：关联引用；[DEV-005 会话流式问答](../开发/DEV-005-会话流式问答.md)
- 关系：关联依赖；[DEV-012 服务托管与启动停止](../开发/DEV-012-服务托管与启动停止.md)
- 关系：关联引用；[OPS-003 设置密钥与故障排查](../运维/OPS-003-设置密钥与故障排查.md)
- 关系：测试验证；[TEST-004 标签与React交互](../测试/TEST-004-标签与React交互.md)
- 关系：测试验证；[TEST-005 设置与Trace回归](../测试/TEST-005-设置与Trace回归.md)
- 关系：测试验证；[TEST-007 缺陷与历史反馈追踪](../测试/TEST-007-缺陷与历史反馈追踪.md)

## 变更记录

| 日期 | 内容 | 原因 |
|---|---|---|
| 2026-08-27 | 从当前代码与原资料整理，补充证据边界和双向链接 | 建立独立new_docs对照体系，保留原文件 |
