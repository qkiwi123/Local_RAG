# DEV-010 检索Trace与配置追溯

## 文档信息

- 文档ID：DEV-010
- 文档类型：开发
- 状态：已完成
- 创建日期：2026-08-27
- 最后更新：2026-09-12
- 证据口径：2026-09-12在既有`chat-v2`上增加回答/改写用途级生成配置摘要和改写关闭状态；全量结果见[TEST-005](../测试/TEST-005-设置与Trace回归.md#evidence)。未连接真实外部兼容端点或生产代理。
- 导航：[本类目录](./README.md) · [文档中心](../README.md)

## 1. 目标与来源

关系：实现[Trace契约](../设计/DES-006-运行时设置与Trace.md#trace)和[DES-003 检索融合与上下文选择](../设计/DES-003-检索融合与上下文选择.md)，用真实候选链解释请求参数，不改动排序。

## 2. 代码与结构

[retrieval_trace.py](../../backend/services/retrieval_trace.py)：TraceCollector、build_trace_effective_config、finalize_chat_trace；
[schemas.py](../../backend/models/schemas.py)：RetrievalTrace及子模型；
[retriever.py](../../backend/services/retriever.py)：采集阶段；
[chat.py](../../backend/routers/chat.py)：请求级补充；
[conversation_store.py](../../backend/services/conversation_store.py)：绑定助手消息保存；
[TraceDrawer.tsx](../../web/src/components/TraceDrawer.tsx)：显示。

<a id="implementation"></a>
## 3. 核心逻辑

完整chunk按ID去重复用，阶段列表保存排名、分数、eligible和filtered_reason，最终selected对应真实上下文。只有实际执行的阶段出现在Trace中。

effective_config在_runtime_config完成后从实际对象构造；聊天使用同一冻结snapshot补settings_revision和LLM摘要。llm.called表示尝试调用改写或生成，失败尝试仍算调用；`llm.answer`和`llm.rewrite`分别记录called、配置的思考模式/强度、Temperature、最大完成Tokens及超时。仅检索Trace的revision/llm可为null。

`chat-v2`新增五个可选耗时字段和`followup_rewrite_status`；2026-09-12为状态增加`disabled`，区别于首轮/无历史的`not_applicable`。旧会话不迁移Trace JSON，新增用途级配置为可选并由Drawer显示“旧Trace未记录”。同步/流式`total`统一为请求入口至持久化前，持久化和重放保持首次请求版本。模型字段严格限定，禁止Key、Base URL、授权头、问题全文和原始思考文本等额外内容。

## 4. 存储与风险

Trace在message_traces中按助手消息保存，随会话删除级联清理，容量超过TRACE_STORAGE_MAX_BYTES按创建时间淘汰最旧记录。代码按创建时间清理，不应把“LRU”历史称呼误读成每次访问刷新时间的严格LRU实现。

关闭RETRIEVAL_TRACE_ENABLED时不收集/返回/持久化。开关关闭只影响新请求，不能据此认为旧候选正文已从磁盘删除。

<a id="limitations"></a>
## 5. 实际结果与计时限制

S01—S10相关自动化已运行；历史回归见[TEST-005](../测试/TEST-005-设置与Trace回归.md#evidence)。流式路径现已填写实际`context_formatting`与`llm_generation`，同步/流式新Trace使用相同`chat-v2 total`起止点。未执行字段为`null`，执行过的0保留为有效测量，不能再把二者混同。

2026-09-09实施前诊断样本仍作为历史证据保留：生产持久化Trace一例`total=5668ms`、已知阶段约1136ms、旧前端差值4532ms、答案419字。该旧Trace没有`chat-v2`版本，不能用新合同回算或与新序列混合。新合同直接测量改写、引用提取、流式生成和服务端delta里程碑；`persistence`固定为`complete_chat`会话提交事务且位于`total`之后，不包含随后Trace写入，避免计量自身的自引用。

TraceDrawer现将同边界阶段、边界外会话提交和用户可见里程碑分区。旧“其他阶段（追问改写/历史/引用提取/持久化等）”已删除；只有`chat-v2`可显示“总耗时内未拆分 / 调度开销”，旧Trace不计算差值。字段语义见[DES-006计时合同](../设计/DES-006-运行时设置与Trace.md#timing)与[OPT-006](../优化/OPT-006-Trace计量拆分与首字延迟优化.md#display)。

用途级摘要记录的是项目配置和调用状态，不证明上游实际采用了参数；OpenAI-compatible端点可能忽略或拒绝思考参数。当前没有强制请求流式usage，也不记录实际输入/思考/正文token或finish_reason；不能把`max_tokens`误读为实际消耗。Trace是诊断数据，不等于没有访问控制风险；候选完整原文可包含敏感资料。运维见[OPS-003 设置密钥与故障排查](../运维/OPS-003-设置密钥与故障排查.md)。

## 关联文档

- 关系：关联引用；[DES-001 系统架构与存储边界](../设计/DES-001-系统架构与存储边界.md)
- 关系：来源设计；[DES-003 检索融合与上下文选择](../设计/DES-003-检索融合与上下文选择.md)
- 关系：来源设计；[DES-006 运行时设置与Trace](../设计/DES-006-运行时设置与Trace.md)
- 关系：关联引用；[DEV-005 会话流式问答](../开发/DEV-005-会话流式问答.md)
- 关系：关联引用；[DEV-009 运行时设置与密钥](../开发/DEV-009-运行时设置与密钥.md)
- 关系：运维；[OPS-003 设置密钥与故障排查](../运维/OPS-003-设置密钥与故障排查.md)
- 关系：缺陷追踪；[BUG-004 流式响应缓冲至完成时一次性输出](../测试/BUG-004-流式响应缓冲至完成时一次性输出.md)
- 关系：计量优化；[OPT-006 Trace计量拆分与首字延迟优化](../优化/OPT-006-Trace计量拆分与首字延迟优化.md)
- 关系：测试验证；[TEST-002 检索质量与性能](../测试/TEST-002-检索质量与性能.md)
- 关系：测试验证；[TEST-005 设置与Trace回归](../测试/TEST-005-设置与Trace回归.md)
- 关系：关联引用；[TEST-007 缺陷与历史反馈追踪](../测试/TEST-007-缺陷与历史反馈追踪.md)

## 变更记录

| 日期 | 内容 | 原因 |
|---|---|---|
| 2026-09-12 | 增加回答/改写配置摘要、用途级called及`disabled`状态，前端显示并保持旧Trace可读 | 让运行时生成控制可追溯，同时不把配置上限伪装成实际token消耗或模型已采纳参数 |
| 2026-09-09 | 实施`chat-v2`、五项可选计量、改写状态、统一total、持久化/重放、纯检索评测兼容及Drawer分区显示；全量回归通过 | 完成OPT-006方向一并停止旧差值的确定性归因 |
| 2026-09-09 | 补记全量自动化回归，明确BUG-004传输实现已改但Trace字段未改 | 防止把同轮代码修复误写成Trace计量拆分已经实施 |
| 2026-08-27 | 从当前代码与原资料整理，补充证据边界和双向链接 | 建立独立new_docs对照体系，保留原文件 |
| 2026-09-09 | 计时限制补充2026-09-09量化口径：差值桶主体为LLM生成、改写与持久化不在total内 | BUG-004诊断实测数据落档，关联OPT-006计量拆分方向 |
