# TEST-005 设置与Trace回归

## 文档信息

- 文档ID：TEST-005
- 文档类型：测试
- 状态：测试中
- 创建日期：2026-08-27
- 最后更新：2026-09-10
- 证据口径：2026-09-09已完成OPT-006方向一专项与全量自动化回归；2026-09-10补充方案A查询改写参数、提示词、服务商隔离和回答调用不受影响测试，相关专项28项、后端全量828项/3项跳过、前端41项、lint和构建通过，并执行一条合成真实DeepSeek请求。生产代理、人工浏览器和长期流量未验收。
- 导航：[本类目录](./README.md) · [文档中心](../README.md)

## 1. 目标与环境

关系：验证[DEV-009 运行时设置与密钥](../开发/DEV-009-运行时设置与密钥.md)、[DEV-010 检索Trace与配置追溯](../开发/DEV-010-检索Trace与配置追溯.md)及[DEV-008 React主前端](../开发/DEV-008-React主前端.md)设置/Trace交互。使用隔离SQLite、DPAPI替身与mock模型；历史浏览器使用构造Trace，不调用真实问答模型。

<a id="cases"></a>
## 2. 用例

| 用例 | 输入/操作 | 预期 | 代码 | 本次实际 |
|---|---|---|---|---|
| S01 | 保存、旧revision、失败提交 | 快照固定、冲突拒绝、不半提交 | [runtime](../../tests/test_runtime_settings.py) | 2026-09-09全量通过 |
| S02 | Key三态、敏感422 | 不回显、不写普通SQLite | [secret](../../tests/test_secret_store.py)、[router](../../tests/test_settings_router.py) | 2026-09-09全量通过 |
| S03 | GET状态 | 不冷加载模型 | [router](../../tests/test_settings_router.py) | 2026-09-09全量通过 |
| S04 | 草稿测试、reset | 使用草稿、不改.env/索引 | [LLM](../../tests/test_llm_runtime.py)、[设置页](../../web/src/__tests__/settingsPage.test.tsx) | 2026-09-09全量通过 |
| S05 | 保存前后chat/重放 | 首次revision/实际参数一致 | [API](../../tests/test_api.py)、[retrieval](../../tests/test_retrieval_runtime.py) | 2026-09-09全量通过 |
| S06 | LLM关闭/先改写后空检索 | called准确、无Key | [Trace](../../tests/test_retrieval_trace.py) | 2026-09-09全量通过 |
| S07 | 旧Trace缺新字段 | 可读并显示未记录 | [Drawer](../../web/src/__tests__/traceDrawer.test.tsx) | 2026-09-09全量通过 |
| S08 | 启用refusal gate | 校准前拒绝 | [runtime](../../tests/test_runtime_settings.py) | 2026-09-09全量通过 |
| S09 | 新增改写/引用提取/持久化/首字里程碑字段 | 已执行、未执行、不适用和旧Trace分别有明确语义，不用0伪造测量 | [OPT-006验收](../优化/OPT-006-Trace计量拆分与首字延迟优化.md#acceptance) | 2026-09-09通过：成功/失败改写、无检索、LLM关闭、持久化/非持久化及旧Trace均覆盖 |
| S10 | 同步/流式总耗时边界 | `chat-v2`统一请求入口至持久化前；持久化独立显示，不把差值归因成阶段 | [DEV-010限制](../开发/DEV-010-检索Trace与配置追溯.md#limitations)、[OPT-006方案](../优化/OPT-006-Trace计量拆分与首字延迟优化.md#plan) | 2026-09-09通过：Drawer分区、旧Trace不算差值、纯检索评测省略聊天null字段 |

<a id="evidence"></a>
## 3. 历史执行证据：2026-08-25 S6

来源：[设置方案第20.7节](../../docs/前端设置功能优化方案.md#207-自动化测试和结果)及其后浏览器记录。

| 范围 | 原记录结果 | 限制 |
|---|---|---|
| 后端定向 | 83 passed，11.11s | 指定API/Trace/会话集合 |
| Trace Drawer | 2 passed，2.32s | 组件验证 |
| 后端全量 | 606 passed、3 skipped、7 warnings，44.01s | 3个真实LLM测试未执行 |
| 前端全量 | 9 files / 39 tests passed | 非浏览器全链路 |
| lint/TS/build | 通过 | 大chunk和jsdom提示仍在 |
| 浏览器 | 1440×900、390×844通过 | 构造Trace，未验真实模型效果 |

本次读取原记录并核对S6实现，没有重跑；不得标为2026-08-27新结果。

### 2026-09-09当前回归

- 后端全量：821 passed、3 skipped；首次使用系统临时目录时，v3.1发布器路径前置条件导致1项环境失败，改用该测试要求的仓库内独立临时目录后全量通过。
- 前端全量：9个测试文件、41项测试通过；lint与生产构建通过。构建仍提示主JavaScript chunk超过500 kB。
- BUG-004专项：真实本地TCP/uvicorn逐事件测试验证3个delta分批到达，并验证检索同步阶段阻塞时健康检查仍可响应；异步上游流缓存、取消关闭和前端响应头前Abort均有自动化覆盖。
- 边界：未连接外部LLM、未经过生产反向代理，也未执行人工浏览器验收；这些结果不能外推为生产首字延迟指标。

### 2026-09-09 OPT-006方向一回归

- 后端全量：827 passed、3 skipped；使用v3.1评测要求的仓库内全新`--basetemp`，无产品失败。
- 前端全量：9个测试文件、41项通过；lint与生产构建通过，保留主JavaScript chunk超过500 kB的既有警告。
- 路径矩阵：同步/流式、改写成功/失败、无检索、LLM关闭、持久化/非持久化、会话加载/重放、旧Trace及纯检索评测兼容均通过。
- 显示：`chat-v2`阶段、边界外会话提交和服务端里程碑分区；旧Trace不计算差值，不再出现确定性的“其他阶段”归因。

## 4. 复跑与外部门禁

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp .pytest-tmp\docs-settings-check tests/test_runtime_settings.py tests/test_settings_router.py tests/test_retrieval_trace.py tests/test_retrieval_runtime.py tests/test_api.py tests/test_chat_stream.py -q
```

真实连接测试需显式允许，dummy Key不能证明认证成功。CPU隔离验收显式关闭RERANKER_USE_FP16，历史曾因继承GPU配置导致启动校验失败。

实施前缺口已由`chat-v2`修复：流式有结果路径写入实际上下文与生成墙钟耗时，改写和引用提取独立计量；同步/流式`total`统一为请求入口至持久化前。`persistence`只计`complete_chat`事务并独立于`total`，不含Trace自身写入。一次旧生产Trace差值仍不能回算成新字段或替代正式性能测量。

## 5. 结论与待验收

S01—S10均通过2026-09-09自动化回归；2026-08-25历史工程验证继续作为独立证据保留。OPT-006方向一已完成，方向二方案A已实施。LLM专项确认普通回答不附加DeepSeek思考扩展，通用OpenAI-compatible改写也不接收该私有字段；多进程热同步、生产代理、人工浏览器和长期外部可用性本次未验证。

## 关联文档

- 关系：关联引用；[DES-006 运行时设置与Trace](../设计/DES-006-运行时设置与Trace.md)
- 关系：关联引用；[DEV-005 会话流式问答](../开发/DEV-005-会话流式问答.md)
- 关系：验证对象；[DEV-008 React主前端](../开发/DEV-008-React主前端.md)
- 关系：验证对象；[DEV-009 运行时设置与密钥](../开发/DEV-009-运行时设置与密钥.md)
- 关系：验证对象；[DEV-010 检索Trace与配置追溯](../开发/DEV-010-检索Trace与配置追溯.md)
- 关系：关联引用；[REQ-004 运行时设置与诊断](../需求/REQ-004-运行时设置与诊断.md)
- 关系：关联引用；[TEST-003 会话引用与流式协议](../测试/TEST-003-会话引用与流式协议.md)
- 关系：关联引用；[TEST-004 标签与React交互](../测试/TEST-004-标签与React交互.md)
- 关系：关联引用；[TEST-007 缺陷与历史反馈追踪](../测试/TEST-007-缺陷与历史反馈追踪.md)
- 关系：缺陷回归；[BUG-004 流式响应缓冲至完成时一次性输出](../测试/BUG-004-流式响应缓冲至完成时一次性输出.md)
- 关系：后续优化验证；[OPT-006 Trace计量拆分与首字延迟优化](../优化/OPT-006-Trace计量拆分与首字延迟优化.md#acceptance)

## 变更记录

| 日期 | 内容 | 原因 |
|---|---|---|
| 2026-09-10 | 增加DeepSeek改写关闭思考、严格提示词、普通回答和通用兼容服务商隔离断言 | 防止方案A误改变最终回答或破坏非DeepSeek兼容接口 |
| 2026-09-09 | S09/S10通过；记录`chat-v2`字段矩阵、持久化/重放、旧Trace/评测兼容、Drawer分区及827/41全量结果 | 验收OPT-006方向一并与外部性能和方向二质量决策分开 |
| 2026-09-09 | 补记BUG-004完成后的后端821/前端41项全量结果、真实本地TCP逐事件回归与外部边界；S09/S10保持未执行 | 区分已完成的传输修复与尚未实施的Trace计量扩展 |
| 2026-09-09 | 增加S09/S10计量边界用例，记录流式0值和前端差值桶的当前缺口，修正S6提交状态表述，并关联BUG-004/OPT-006 | 将历史设置回归与本次尚未执行的Trace计量/流式时序验收分开 |
| 2026-08-27 | 从当前代码与原资料整理，补充证据边界和双向链接 | 建立独立new_docs对照体系，保留原文件 |
