# DEV-008 React主前端

## 文档信息

- 文档ID：DEV-008
- 文档类型：开发
- 状态：已完成
- 创建日期：2026-08-27
- 最后更新：2026-09-12
- 证据口径：2026-09-12设置页新增回答/查询改写生成参数分组、通用兼容提示，TraceDrawer新增两类配置快照；自动化结果见[TEST-005](../测试/TEST-005-设置与Trace回归.md#evidence)。未做人工浏览器多视口或真实外部LLM验收。
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
| [ModelSettingsCard.tsx](../../web/src/pages/settings/ModelSettingsCard.tsx) / [SettingsPage.tsx](../../web/src/pages/settings/SettingsPage.tsx) | 模型连接、回答/改写生成参数与revision PATCH |

<a id="implementation"></a>
## 3. 开发逻辑与实际行为

聊天提交后立即显示待发送用户消息，再显示思考/流式助手状态；完成后服务端消息替换本地临时状态。失败重试复用request_id并设置retry_failed；409先同步会话，不自动重发问题。

知识库默认全宽文档工作区；上传和标签抽屉按需打开；队列关闭后保留。上传只重试失败项，OCR warning作为AI识别风险显示。检索范围位于输入框上方，下一请求带tags。

桌面支持会话栏和抽屉调宽，窄屏使用卡片、移动会话抽屉或全宽任务面板。UI尺寸偏好与后端运行设置分开。

模型设置页将连接参数、最终回答和查询改写分区。思考模式和强度使用通用枚举，Temperature与Token上限始终可编辑；页面只说明部分模型可能忽略、不支持或重新解释参数，不依据当前模型名锁定控件。查询改写开关显示为自动/关闭。该界面没有思考文本显示控件，聊天流继续只渲染最终答案delta。

## 4. 实际结果与验证

当前代码与历史F1—F4/S5/S6记录相符。BUG-004和OPT-006方向一实施后前端全量仍为9个文件41项并全部通过，lint及`tsc -b && vite build`通过；构建仍有既有的大chunk警告。本模块用例与视口矩阵见[TEST-004 标签与React交互](../测试/TEST-004-标签与React交互.md)。

`api/stream.ts`继续使用`ReadableStream.getReader()`、流式`TextDecoder`和跨chunk缓冲，完整NDJSON行一到达就分派`onDelta`。新增测试在第二块尚未释放、响应Promise尚未完成时断言第一块已交给控制器；另补齐`fetch`等待响应头期间Abort的识别，主动停止不再落入普通错误提示。后端集中到达根因和真实HTTP修复证据见[BUG-004](../测试/BUG-004-流式响应缓冲至完成时一次性输出.md#regression)与[TEST-003](../测试/TEST-003-会话引用与流式协议.md#cases)。

## 5. Bug修复记录

反馈21的布局重叠并非首次报告“已修复”时就被验证；后续重构和多视口验收才形成证据。反馈22/23/24分别对应即时消息、面板调宽、输入区范围位置，详见[历史反馈](../测试/TEST-007-缺陷与历史反馈追踪.md#feedback)及本节代码地图。

## 6. 已知限制与后续

构建仍有较大单chunk警告，暂无拆包前后数据；jsdom流测试和真实本地HTTP测试都不替代人工浏览器布局/体感验收。HTTP 200不足以证明React挂载，MIME故障由[托管修复](../开发/DEV-012-服务托管与启动停止.md#bug-fixes)处理。TraceDrawer已移除旧“其他阶段”确定性归因，按`chat-v2`分为同边界阶段、边界外提交和服务端里程碑，并显示回答/改写冻结的思考模式、强度、Temperature、Token上限、超时和调用状态；旧Trace明确未记录。浏览器实际首字仍需单独测量。旧frontend/仍保留，未授权删除。

## 关联文档

- 关系：来源设计；[DES-004 会话流式协议与来源预览](../设计/DES-004-会话流式协议与来源预览.md)
- 关系：来源设计；[DES-005 标签与React交互](../设计/DES-005-标签与React交互.md)
- 关系：关联引用；[DEV-005 会话流式问答](../开发/DEV-005-会话流式问答.md)
- 关系：关联依赖；[DEV-012 服务托管与启动停止](../开发/DEV-012-服务托管与启动停止.md)
- 关系：缺陷跟踪；[BUG-004 流式响应缓冲至完成时一次性输出](../测试/BUG-004-流式响应缓冲至完成时一次性输出.md)
- 关系：计量优化；[OPT-006 Trace计量拆分与首字延迟优化](../优化/OPT-006-Trace计量拆分与首字延迟优化.md#display)
- 关系：关联引用；[OPS-003 设置密钥与故障排查](../运维/OPS-003-设置密钥与故障排查.md)
- 关系：测试验证；[TEST-004 标签与React交互](../测试/TEST-004-标签与React交互.md)
- 关系：测试验证；[TEST-005 设置与Trace回归](../测试/TEST-005-设置与Trace回归.md)
- 关系：测试验证；[TEST-007 缺陷与历史反馈追踪](../测试/TEST-007-缺陷与历史反馈追踪.md)

## 变更记录

| 日期 | 内容 | 原因 |
|---|---|---|
| 2026-09-12 | 设置页增加回答/改写生成分组和通用兼容提示，保持思考开启时Temperature可编辑；TraceDrawer增加用途级配置摘要 | 满足跨模型配置需求，不引入原始思考文本展示 |
| 2026-09-09 | 实施`chat-v2` TraceDrawer分区、旧Trace不归因、新字段/状态/里程碑显示并通过41项、lint和构建 | 完成OPT-006方向一的前端呈现和兼容验收 |
| 2026-09-09 | 增加未完成响应中的即时delta分派和响应头前Abort测试，修正主动停止识别；记录41项、lint与构建结果 | 配合BUG-004后端异步化，验证前端不会自行等到响应结束才更新，并消除停止阶段误报 |
| 2026-09-09 | 补充前端NDJSON解析与网络增量到达的边界，登记BUG-004和OPT-006的前置关系 | 解析器能处理分块不等于后端及时flush；Trace差值显示也需要新的字段契约 |
| 2026-08-27 | 从当前代码与原资料整理，补充证据边界和双向链接 | 建立独立new_docs对照体系，保留原文件 |
