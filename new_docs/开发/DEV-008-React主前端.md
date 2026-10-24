# DEV-008 React主前端

## 文档信息

- 文档ID：DEV-008
- 文档类型：开发
- 状态：已完成
- 创建日期：2026-08-27
- 最后更新：2026-10-24
- 证据口径：2026-10-24手测回切修复后聊天17项、完整前端14文件99项及lint/类型/构建通过；独立真实HTTP和SQLite中4请求/8消息均complete。检索使用6秒固定替身，生产数据与外部模型未操作；前轮97项及后端192项按历史保留。
- 导航：[本类目录](./README.md) · [文档中心](../README.md)

## 1. 目标、来源与模块边界

关系：实现[React工作区](../设计/DES-005-标签与React交互.md#interaction)和[流式客户端](../设计/DES-004-会话流式协议与来源预览.md#stream)。负责用户交互，不自行访问SQLite、Chroma或外部模型。

## 2. 代码地图

| 入口 | 职责 |
|---|---|
| [AppShell.tsx](../../web/src/app/AppShell.tsx) / [App.tsx](../../web/src/App.tsx) | 导航与路由 |
| [ThemeProvider.tsx](../../web/src/app/ThemeProvider.tsx) / [ThemeContext.ts](../../web/src/app/ThemeContext.ts) | 日夜主题、Ant Design算法及外观偏好 |
| [RetrievalScopeBar.tsx](../../web/src/pages/chat/RetrievalScopeBar.tsx) / [RetrievalScopePicker.tsx](../../web/src/pages/chat/RetrievalScopePicker.tsx) | 输入工具栏资料入口、搜索即选的扁平列表及父级归一化 |
| [useChatController.ts](../../web/src/pages/chat/useChatController.ts) | 消息、发送、停止、重试和409重同步 |
| [ChatPage.tsx](../../web/src/pages/chat/ChatPage.tsx) / [RetrievalScopeBar.tsx](../../web/src/pages/chat/RetrievalScopeBar.tsx) | 会话布局与范围 |
| [KnowledgePage.tsx](../../web/src/pages/knowledge/KnowledgePage.tsx) / [useUploadQueue.ts](../../web/src/pages/knowledge/useUploadQueue.ts) | 文档与逐文件队列 |
| [ResizableDrawer.tsx](../../web/src/components/ResizableDrawer.tsx) / [usePanelResize.ts](../../web/src/components/usePanelResize.ts) | 可调任务面板 |
| [api/stream.ts](../../web/src/api/stream.ts) | NDJSON分块解析 |
| [ModelSettingsCard.tsx](../../web/src/pages/settings/ModelSettingsCard.tsx) / [SettingsPage.tsx](../../web/src/pages/settings/SettingsPage.tsx) | 模型连接、回答/改写生成参数与revision PATCH |

<a id="implementation"></a>
## 3. 开发逻辑与实际行为

聊天提交后立即显示待发送用户消息，再显示思考/流式助手状态；完成后服务端消息替换本地临时状态。失败重试复用request_id并设置retry_failed；409先同步会话，不自动重发问题。

知识库默认全宽文档工作区；上传和标签抽屉按需打开；队列关闭后保留。上传只重试失败项，OCR warning作为AI识别风险显示。检索范围位于输入框内的工具栏，下一请求带tags。

2026-10-23修复批量选择重复入队：`UploadWorkflowDrawer`按`beforeUpload`的首个文件回调处理整批并返回`Upload.LIST_IGNORE`，不再从逐文件`onChange`反复加入整批。真实重复、格式和大小问题汇总为面板内单条提示，原因默认折叠、展开列表最多160 px高，下一次合法选择清除旧提示。文件去重规则继续使用名称、大小和最后修改时间；这不是知识库内容级去重。

设置页使用检索、问答模型、视觉解析、运行状态四分区及固定导航；BM25召回Top K与Dense召回数量默认可见，流程预览跟随草稿，已保存概览与草稿分开。知识库增加当前列表的片段/原文件大小概览、格式图标、直接点击文件名预览和清除筛选；全局主题、导航、对话空态统一。详情与验证见[TEST-004本轮记录](../测试/TEST-004-标签与React交互.md#batch-selection)和[TEST-005设置回归](../测试/TEST-005-设置与Trace回归.md#settings-polish)。

桌面支持会话栏和抽屉调宽，窄屏使用卡片、移动会话抽屉或全宽任务面板。UI尺寸偏好与后端运行设置分开。

2026-10-24补充：顶部日夜按钮在首次无偏好时跟随系统，随后将用户选择保存到`rag.appearance.theme`；Ant Design组件和页面、文档预览、提示消息使用一致的主题色。仅保存外观偏好，不写后端配置。切换主题不重建页面，保留暂存文件。

本日后续范围迭代用输入框工具栏的“全部资料/当前范围”入口替换摘要卡。可搜索扁平列表直接多选生效，已选项可删除；选择父级合并子级，继承项显示已包含。搜索只筛选可见行，不裁剪当前值，仍以原`tags`参数提交；方向键选行、Escape收起并恢复入口焦点，列表与已选区分别限高。首版树形方案的79项证据作为历史保留。

上传面板将确认、重试、清空固定在独立顶部操作栏，下方滚动区域合并文件选择、标签与队列。`useUploadQueue`在批次结束后移除该批成功项，保留失败项及期间新增文件，结果摘要和OCR等处理警告单独保留。清空只影响临时队列，上传中禁止清空；队列引用同步更新，避免连续操作读取旧队列。状态及证据见[TEST-004](../测试/TEST-004-标签与React交互.md#theme-upload-workflow)。

模型设置页将连接参数、最终回答和查询改写分区。思考模式和强度使用通用枚举，Temperature与Token上限始终可编辑；页面只说明部分模型可能忽略、不支持或重新解释参数，不依据当前模型名锁定控件。查询改写开关显示为自动/关闭。当前聊天同时展示reasoning_delta和正文delta，正文首块到达默认折叠思考，仍可手动展开；成功历史思考默认折叠。

09-18已增加视觉模型跟随问答/自定义配置，OCR显式左图右文分组和抽屉动画/图片加载后高亮定位；启动默认构建React并以缓存戳打开。09-19增加“本轮检索来源”及正文已引用/未引用分组，沿用原chunk预览，设置页明确“不启用硬拒答门控”。操作记录见[视觉与会话优化记录](../../docs/2026-09-18-视觉与会话体验优化记录.md)，当前回归见[TEST-003](../测试/TEST-003-会话引用与流式协议.md#current-policy)。

<a id="chat-composer"></a>
### 对话输入区的两种布局

`ChatPage`依据历史消息、待发送问题、流式和错误状态选择welcome/conversation；草稿文本不参与切换。空白时间线仅保留短标题，输入区下方弹性留白收缩后将同一个`ChatComposer`移到底部，不重新挂载。留白flex-grow过渡420 ms，减少动画偏好禁用；消息与输入区共用阅读列，当前后续迭代上限为920 px，主区弱化卡片边框。输入框内聚合资料范围、发送与停止工具，textarea自动增长2—6行，Shift+Enter换行，输入法isComposing或229键码不会提交。

该阶段86项、lint/构建及本机浏览器验证入口见[TEST-004](../测试/TEST-004-标签与React交互.md#chat-composer)。浏览器使用相同生产构建加隔离fetch模拟验证发送，无生产会话写入和真实外部模型调用；已有真实会话及资料菜单另做只读检查。

<a id="citation-layout"></a>
### 长引用的宽度约束修复

本日后续截图复现回答气泡宽约654 px、内容scrollWidth=820 px，原文按钮向右越界约159 px。原因是引用摘要固定640 px上限与List的不可收缩文字/独立actions区域叠加。

[引用组件](../../web/src/components/CitationList.tsx)改为同一列表行内的内容和预览按钮，[引用样式](../../web/src/components/citation-list.css)用minmax(0,1fr)加auto列、内容min-width:0约束。标题/位置允许长路径换行，摘要按可用宽度最多两行；620 px以下改为单列，按钮位于内容下方。来源分组、编号、chunk目标与预览定位逻辑保留。定向26项及实际历史会话桌面/窄屏结果见[TEST-004](../测试/TEST-004-标签与React交互.md#citation-layout)，本轮没有重跑此前86项全量。

<a id="answer-column"></a>
### 无外框AI回复与输入对齐

`MessageTimeline`的助手内容使用独立`chat-answer`，宽100%、无外框和底色，用户内容继续使用气泡；两者仍保留相同request_id/role键，思考/流式/失败/引用逻辑未改。`chat-content-column`同时包住时间线内容和输入区，统一最大920 px；两个外层均用`scrollbar-gutter: stable`，输入区通过隐藏溢出的滚动容器预留相同槽宽，避免仅按max-width居中造成约半个滚动条宽的偏差。

助手正文桌面15 px/1.85行高、窄屏14 px/1.8，轮次间距28 px；引用上方细分隔线和留白替代气泡分区。真实1280/390/1600 px视口边界与定向26项结果见[TEST-004](../测试/TEST-004-标签与React交互.md#answer-column)，前轮86项全量仍为历史记录。

## 4. 实际结果与验证

2026-10-24提交前：最终前端14文件86项、lint、TypeScript与生产构建重新通过，覆盖本日全部UI改动；实现及各阶段浏览器/Mock边界按[TEST-004提交前记录](../测试/TEST-004-标签与React交互.md#frontend-commit)区分。以下41项等仍为相应历史阶段记录。

当前代码与历史F1—F4/S5/S6记录相符。BUG-004和OPT-006方向一实施后前端全量仍为9个文件41项并全部通过，lint及`tsc -b && vite build`通过；构建仍有既有的大chunk警告。本模块用例与视口矩阵见[TEST-004 标签与React交互](../测试/TEST-004-标签与React交互.md)。

`api/stream.ts`继续使用`ReadableStream.getReader()`、流式`TextDecoder`和跨chunk缓冲，完整NDJSON行一到达就分派`onDelta`。新增测试在第二块尚未释放、响应Promise尚未完成时断言第一块已交给控制器；另补齐`fetch`等待响应头期间Abort的识别，主动停止不再落入普通错误提示。后端集中到达根因和真实HTTP修复证据见[BUG-004](../测试/BUG-004-流式响应缓冲至完成时一次性输出.md#regression)与[TEST-003](../测试/TEST-003-会话引用与流式协议.md#cases)。

## 5. Bug修复记录

反馈21的布局重叠并非首次报告“已修复”时就被验证；后续重构和多视口验收才形成证据。反馈22/23/24分别对应即时消息、面板调宽、输入区范围位置，详见[历史反馈](../测试/TEST-007-缺陷与历史反馈追踪.md#feedback)及本节代码地图。

## 6. 已知限制与后续

构建仍有较大单chunk警告，暂无拆包前后数据；jsdom流测试和真实本地HTTP测试都不替代人工浏览器布局/体感验收。HTTP 200不足以证明React挂载，MIME故障由[托管修复](../开发/DEV-012-服务托管与启动停止.md#bug-fixes)处理。TraceDrawer已移除旧“其他阶段”确定性归因，按`chat-v2`分为同边界阶段、边界外提交和服务端里程碑，并显示回答/改写冻结的思考模式、强度、Temperature、Token上限、超时和调用状态；旧Trace明确未记录。浏览器实际首字仍需单独测量。旧frontend/仍保留，未授权删除。

<a id="conversation-followup"></a>
### 2026-10-24会话后续修复

[useChatController](../../web/src/pages/chat/useChatController.ts)的`createConversation`只切换本地空白视图，首条`send`才创建后端记录；每个会话用同步请求引用防重复发送。导航按当前会话显示pending/streaming/text/reasoning/error，原会话在后台继续生成；创建、历史和列表的迟到响应不抢回当前视图。显式空白页在刷新后保留。创建与流式提交仍是两个接口，极端中断窗口不自动删除服务端已有历史。此前“导航即中止”实现已由下方修复替代。

[ConversationRail](../../web/src/pages/chat/ConversationRail.tsx)共用滚动容器使新建与选中条目等宽，并固定新建操作；[样式](../../web/src/pages/chat/chat-page.css)统一输入与用户消息四角。[CitationList](../../web/src/components/CitationList.tsx)用默认关闭的本地展开状态、数量和aria-expanded/controls，展开才挂载分组和预览按钮。

[AppShell](../../web/src/app/AppShell.tsx)轮询健康接口获取后端scope端口，离线清空端口；[main.py](../../backend/main.py)只读返回端口，不扫描或关闭其他程序。设置页另增加[文档分块](./DEV-009-运行时设置与密钥.md#chunking)和[Trace开关](./DEV-009-运行时设置与密钥.md#trace-switch)。最终前端14文件97项、lint/类型/构建通过；本机浏览器的真实设置/会话保存及固定检索替身边界见[TEST-004](../测试/TEST-004-标签与React交互.md#conversation-followup)。

<a id="conversation-return"></a>
### 2026-10-24切走再返回报错修复

根因：上一轮`clearActiveRequest`在历史切换和新建时调用AbortController.abort；后端已有断连合同会将请求/用户消息标记为failed/interrupted，返回原会话因此显示处理失败。前轮只验证离开时动画消失，未验证回到原会话的保存状态；旧隔离库中实际保留了2条interrupted证据。

修复将生成请求与可见页面分开：`requestsRef`按会话/独立草稿key保存ActiveRun，`runs`保存该请求的文本、思考、问题及错误；导航只改变viewKey。切走后回调继续更新原会话的状态，后台完成仍走原流接口正常落库；返回未完成会话恢复对应进度，返回已完成会话读取服务端完整消息。不同会话可独立生成，同一会话同步防重复。创建尚未返回时切走，创建结果和问题继续归属原草稿，不抢回当前页面；每次新空白页有独立key。

“停止生成”仅中止当前会话；用户明确删除会话时清理其请求。离开整个对话组件、刷新或关闭页面仍按既有断连合同处理，本轮没有改为服务端脱离连接的后台任务。已经因上一轮中断保存为failed的消息保留原状态，可使用既有重试按钮，不自动修改为成功。验证见[TEST-004回切](../测试/TEST-004-标签与React交互.md#conversation-return)，设计见[DES-005](../设计/DES-005-标签与React交互.md#conversation-followup)。

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
| 2026-10-24 | 会话切换改为保留后台请求并按会话恢复，停止只作用当前会话 | 手测发现此前取消请求导致返回原会话显示interrupted失败 |
| 2026-10-24 | 修复新建/导航请求状态，统一圆角与会话宽度，折叠来源并显示端口 | 落实后续界面和空会话反馈，隔离迟到回调 |
| 2026-10-24 | 汇总最终实现并完成提交前14文件86项回归 | 与阶段定向检查区分，保留真实浏览器和Mock证据边界 |
| 2026-10-24 | 后续移除AI气泡，使用920 px共用阅读列与匹配滚动条槽 | 用户要求更大阅读区并让回复、来源和输入框对齐 |
| 2026-10-24 | 后续修复长引用标题/摘要与原文按钮撑破回答气泡，新增局部宽度约束和窄屏换行 | 用户实际会话截图暴露短回答验证未覆盖的布局 |
| 2026-10-24 | 后续加入welcome/conversation弹性留白过渡、复用输入框、范围快捷列表及中文输入防误发 | 将独立范围卡收进输入工具栏，减少操作步骤与空白页割裂 |
| 2026-10-24 | 新增持久化日夜外观，范围摘要及搜索标签树；上传操作置顶、选择/队列合并、一键清空及成功项自动移除 | 改善长队列操作与对话空间，保留失败重试、处理警告和新增暂存文件 |
| 2026-10-23 | 修复批量入队提示刷屏，统一全局/设置/知识库/对话交互与视觉；补BM25常用入口及草稿预览 | 解决真实反馈，保留原有上传确认、设置revision和密钥机制 |
| 2026-09-19 | 同步视觉设置、思考折叠、预览定位、React启动及检索来源分组 | 修正前轮功能落地后遗留的旧界面说明 |
| 2026-09-12 | 设置页增加回答/改写生成分组和通用兼容提示，保持思考开启时Temperature可编辑；TraceDrawer增加用途级配置摘要 | 满足跨模型配置需求，不引入原始思考文本展示 |
| 2026-09-09 | 实施`chat-v2` TraceDrawer分区、旧Trace不归因、新字段/状态/里程碑显示并通过41项、lint和构建 | 完成OPT-006方向一的前端呈现和兼容验收 |
| 2026-09-09 | 增加未完成响应中的即时delta分派和响应头前Abort测试，修正主动停止识别；记录41项、lint与构建结果 | 配合BUG-004后端异步化，验证前端不会自行等到响应结束才更新，并消除停止阶段误报 |
| 2026-09-09 | 补充前端NDJSON解析与网络增量到达的边界，登记BUG-004和OPT-006的前置关系 | 解析器能处理分块不等于后端及时flush；Trace差值显示也需要新的字段契约 |
| 2026-08-27 | 从当前代码与原资料整理，补充证据边界和双向链接 | 建立独立new_docs对照体系，保留原文件 |
