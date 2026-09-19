# DES-006 运行时设置与Trace

## 文档信息

- 文档ID：DES-006
- 文档类型：设计
- 状态：已完成
- 创建日期：2026-08-27
- 最后更新：2026-09-19
- 证据口径：当前文件静态核对；2026-09-12已实现回答/改写独立生成配置、provider适配、查询改写开关和Trace配置快照；全量结果在[TEST-005](../测试/TEST-005-设置与Trace回归.md#evidence)登记。
- 完成范围：设置快照、Trace安全、旧Trace兼容、`chat-v2`及通用生成控制已完成；跨模型/兼容端点真实调用、生产代理和长期性能仍未验收。
- 导航：[本类目录](./README.md) · [文档中心](../README.md)

## 1. 对应需求与设计目标

关系：实现[REQ-004 运行时设置与诊断](../需求/REQ-004-运行时设置与诊断.md)。用户保存配置后应能解释下一请求的行为，同时保护密钥并兼容已有会话Trace。

<a id="snapshot"></a>
## 2. 配置分层与提交

部署默认来自config与环境；SQLite runtime_settings/history只存非敏感覆盖；Key保存在Windows用户级DPAPI文件。RuntimeSettingsManager合并配置并产生不可变EffectiveSettingsSnapshot，含revision、llm、retrieval、字段来源和排除序列化的SecretStr。

PATCH/RESET使用expected_revision检测冲突；先完整校验，再提交覆盖与密钥操作，失败处理以[runtime_settings.py](../../backend/services/runtime_settings.py)为准。应用启动只创建一个manager；不能外推为多worker热更新一致性方案。

## 3. 白名单与接口

GET /api/settings读取脱敏值；GET /api/settings/runtime-status不冷加载模型；PATCH /api/settings保存；POST /api/settings/reset恢复部署默认；POST /api/settings/test-llm测试未保存草稿。

动态字段包括LLM连接配置、回答生成配置、查询改写生成配置和Dense/BM25/RRF/Reranker/最终配额。回答沿用`temperature/max_tokens/timeout_seconds`并增加`answer_thinking_mode/answer_reasoning_effort`；改写增加`rewrite_enabled/rewrite_thinking_mode/rewrite_reasoning_effort/rewrite_temperature/rewrite_max_tokens/rewrite_timeout_seconds`。所有字段下一请求生效，SQLite仍存JSON覆盖，不需要表结构迁移。

思考模式为`provider_default/enabled/disabled`，思考强度为`provider_default/low/medium/high/max`。界面不根据某个模型名禁用Temperature或其他字段，只给兼容性提示。调用适配层在DeepSeek下用`extra_body.thinking`表达显式开关，在通用OpenAI-compatible下用`reasoning_effort`表达明确开关/强度；明确关闭时模式优先并映射为`none`，明确开启但未选强度时映射为`medium`，两者均为默认时不发送控制。未知兼容端点可能忽略或拒绝参数，必须保留显式错误和Trace，不做静默重试或参数删除。

Embedding模型、维度、路径、切分与设备是部署/重建级配置，不在动态白名单。Key省略表示保持，显式replace/clear才修改；clear不是“使用空字符串猜测意图”。

<a id="trace"></a>
## 4. Trace数据合同与采集点

| 字段 | 来源 | 兼容规则 |
|---|---|---|
| settings_revision | 请求开始冻结的snapshot | 旧Trace为null/缺失 |
| effective_config | _runtime_config解析后的实际RetrievalRuntimeConfig | 不读设置页草稿，不补造旧值 |
| llm.called/provider/model | 本请求是否尝试改写或生成、同一snapshot模型身份 | 无Key/Base URL/secret source |
| llm.answer / llm.rewrite | 同一snapshot中按用途冻结的called、思考模式/强度、Temperature、最大完成Tokens和超时 | 新字段可选；旧Trace为null/缺失，不补造 |

effective_config覆盖mode、final K、Dense K/threshold、BM25 K/k1/b、RRF k、Reranker开关/K、单文档上限、门控状态。离线retrieve_with_trace可有参数而revision/LLM摘要为null。

called指调用尝试，不是调用成功；先尝试追问改写后空检索仍为true。`answer.called`和`rewrite.called`进一步区分用途；`followup_rewrite_status=disabled`表示有历史且LLM开启，但运行时配置明确跳过改写。相同request ID重放使用首次Trace，不套用当前设置。关闭Trace不计算/返回/保存Trace。

<a id="timing"></a>
## 5. Trace计时合同（chat-v2，2026-09-09）

- 新Trace使用`timing_contract_version="chat-v2"`；同步和流式`total`均从请求路由入口开始，到引用提取完成、会话提交开始之前结束。旧Trace版本为`null`，原值不迁移、不重算。
- `followup_rewrite`、`citation_extraction`、`persistence`、`time_to_first_delta`和`delta_window`为可选字段。未执行使用`null`或缺失；执行过但低于精度的0仍是有效测量。`followup_rewrite_status`区分不适用、运行时关闭、成功和失败回退。
- `persistence`只计`complete_chat`事务，位于`total`之外且不含随后Trace写入；这样一次保存即可携带该值，不形成计量自身的自引用。非持久化请求为`null`。
- Drawer将同边界阶段、边界外会话提交和用户可见里程碑分区；旧Trace不计算差值，`chat-v2`剩余差值只称为“总耗时内未拆分 / 调度开销”，不归因给具体阶段。
- 服务端首个delta时间与浏览器实际收到首个delta时间分开记录；Trace只保留必要的诊断摘要，不得写入API Key、授权头、Base URL或异常明文。

<a id="limitations"></a>
## 6. 安全、性能与限制

模型读取/测试结果、校验422均需脱敏；不可将完整snapshot无约束dump到Trace。DPAPI不可用时拒绝持久化密钥，不回退明文。Trace只保存生成参数摘要，不保存思考文本；思考另由会话reasoning_delta传输并在成功消息的reasoning_content中保存，不混入正文或下轮历史。Trace虽不含Key仍含候选全文，需容量限制与访问保护。

拒答门控schema仍拒绝enabled=true，2026-09-19用户明确不启用硬门控，设置页同步此决策；历史实验结果见[OPT-003](../优化/OPT-003-候选深度与拒答门控.md)，没有“隐藏开关可提前启用”。实现[DEV-009 运行时设置与密钥](../开发/DEV-009-运行时设置与密钥.md)、[DEV-010 检索Trace与配置追溯](../开发/DEV-010-检索Trace与配置追溯.md)；验证[TEST-005](../测试/TEST-005-设置与Trace回归.md#cases)。原始实施证据：[S1—S6方案](../../docs/前端设置功能优化方案.md)。

## 关联文档

- 关系：详细实现；[DEV-009 运行时设置与密钥](../开发/DEV-009-运行时设置与密钥.md)
- 关系：详细实现；[DEV-010 检索Trace与配置追溯](../开发/DEV-010-检索Trace与配置追溯.md)
- 关系：关联依赖；[OPT-003 候选深度与拒答门控](../优化/OPT-003-候选深度与拒答门控.md)
- 关系：来源需求；[REQ-004 运行时设置与诊断](../需求/REQ-004-运行时设置与诊断.md)
- 关系：关联依赖；[TEST-005 设置与Trace回归](../测试/TEST-005-设置与Trace回归.md)
- 关系：计量优化；[OPT-006 Trace计量拆分与首字延迟优化](../优化/OPT-006-Trace计量拆分与首字延迟优化.md#plan)

## 变更记录

| 日期 | 内容 | 原因 |
|---|---|---|
| 2026-09-19 | 区分Trace摘要与消息思考存储，更新硬门控产品决策 | 避免沿用09-12不展示思考和等待校准的旧说明 |
| 2026-09-12 | 设计并落地回答/改写独立通用生成字段、provider适配、提示而不禁用的UI原则、关闭状态和Trace配置摘要 | 支持更换模型与兼容端点，避免把设置逻辑固化到单一模型，同时明确不展示原始思考文本 |
| 2026-09-09 | 落地`chat-v2`总耗时边界、五项可选字段、改写状态、持久化自引用规避和Drawer分区合同 | 将已评审设计转为可验证实现并保持旧Trace兼容 |
| 2026-09-09 | 同步BUG-004全量及真实本地TCP回归结果，明确它未新增Trace计量字段 | 区分传输链路修复证据与计量合同后续实现 |
| 2026-09-09 | 补充同步/流式总耗时边界、未执行字段语义、前端差值限制和服务端/客户端首字时间区分 | 将Trace展示从“能读配置”扩展为可审计的计量合同，并避免把未测差值写成阶段耗时 |
| 2026-08-27 | 从当前代码与原资料整理，补充证据边界和双向链接 | 建立独立new_docs对照体系，保留原文件 |
