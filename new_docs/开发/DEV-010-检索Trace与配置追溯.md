# DEV-010 检索Trace与配置追溯

## 文档信息

- 文档ID：DEV-010
- 文档类型：开发
- 状态：已完成
- 创建日期：2026-08-27
- 最后更新：2026-08-27
- 证据口径：当前文件静态核对；历史验证与待验证事项在正文区分，本次未重跑产品测试。
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

effective_config在_runtime_config完成后从实际对象构造；聊天使用同一冻结snapshot补settings_revision和LLM摘要。llm.called表示尝试调用改写或生成，失败尝试仍算调用；仅检索Trace的revision/llm可为null。

新字段optional，旧会话不迁移Trace JSON；Drawer显示“旧Trace未记录”。持久化和重放保持首次请求版本。模型字段严格限定，禁止Key、Base URL、授权头等额外内容。

## 4. 存储与风险

Trace在message_traces中按助手消息保存，随会话删除级联清理，容量超过TRACE_STORAGE_MAX_BYTES按创建时间淘汰最旧记录。代码按创建时间清理，不应把“LRU”历史称呼误读成每次访问刷新时间的严格LRU实现。

关闭RETRIEVAL_TRACE_ENABLED时不收集/返回/持久化。开关关闭只影响新请求，不能据此认为旧候选正文已从磁盘删除。

<a id="limitations"></a>
## 5. 实际结果与计时限制

S6当前实现已核对；历史回归见[TEST-005](../测试/TEST-005-设置与Trace回归.md#evidence)，本次未重新运行。流式路径中context_formatting_ms和llm_generation_ms当前填0，total_ms也有路径计时起点差异；零值不是该阶段耗时为零的测量证据。排查总时延需同时查看请求日志和实际路径，不能要求所有字段机械相加。

Trace是诊断数据，不等于没有访问控制风险；候选完整原文可包含敏感资料。运维见[OPS-003 设置密钥与故障排查](../运维/OPS-003-设置密钥与故障排查.md)。

## 关联文档

- 关系：关联引用；[DES-001 系统架构与存储边界](../设计/DES-001-系统架构与存储边界.md)
- 关系：来源设计；[DES-003 检索融合与上下文选择](../设计/DES-003-检索融合与上下文选择.md)
- 关系：来源设计；[DES-006 运行时设置与Trace](../设计/DES-006-运行时设置与Trace.md)
- 关系：关联引用；[DEV-005 会话流式问答](../开发/DEV-005-会话流式问答.md)
- 关系：关联引用；[DEV-009 运行时设置与密钥](../开发/DEV-009-运行时设置与密钥.md)
- 关系：运维；[OPS-003 设置密钥与故障排查](../运维/OPS-003-设置密钥与故障排查.md)
- 关系：测试验证；[TEST-002 检索质量与性能](../测试/TEST-002-检索质量与性能.md)
- 关系：测试验证；[TEST-005 设置与Trace回归](../测试/TEST-005-设置与Trace回归.md)
- 关系：关联引用；[TEST-007 缺陷与历史反馈追踪](../测试/TEST-007-缺陷与历史反馈追踪.md)

## 变更记录

| 日期 | 内容 | 原因 |
|---|---|---|
| 2026-08-27 | 从当前代码与原资料整理，补充证据边界和双向链接 | 建立独立new_docs对照体系，保留原文件 |
