# DES-006 运行时设置与Trace

## 文档信息

- 文档ID：DES-006
- 文档类型：设计
- 状态：已完成
- 创建日期：2026-08-27
- 最后更新：2026-08-27
- 证据口径：当前文件静态核对；历史验证与待验证事项在正文区分，本次未重跑产品测试。
- 导航：[本类目录](./README.md) · [文档中心](../README.md)

## 1. 对应需求与设计目标

关系：实现[REQ-004 运行时设置与诊断](../需求/REQ-004-运行时设置与诊断.md)。用户保存配置后应能解释下一请求的行为，同时保护密钥并兼容已有会话Trace。

<a id="snapshot"></a>
## 2. 配置分层与提交

部署默认来自config与环境；SQLite runtime_settings/history只存非敏感覆盖；Key保存在Windows用户级DPAPI文件。RuntimeSettingsManager合并配置并产生不可变EffectiveSettingsSnapshot，含revision、llm、retrieval、字段来源和排除序列化的SecretStr。

PATCH/RESET使用expected_revision检测冲突；先完整校验，再提交覆盖与密钥操作，失败处理以[runtime_settings.py](../../backend/services/runtime_settings.py)为准。应用启动只创建一个manager；不能外推为多worker热更新一致性方案。

## 3. 白名单与接口

GET /api/settings读取脱敏值；GET /api/settings/runtime-status不冷加载模型；PATCH /api/settings保存；POST /api/settings/reset恢复部署默认；POST /api/settings/test-llm测试未保存草稿。

动态字段包括LLM配置和Dense/BM25/RRF/Reranker/最终配额。Embedding模型、维度、路径、切分与设备是部署/重建级配置，不在动态白名单。Key省略表示保持，显式replace/clear才修改；clear不是“使用空字符串猜测意图”。

<a id="trace"></a>
## 4. Trace数据合同与采集点

| 字段 | 来源 | 兼容规则 |
|---|---|---|
| settings_revision | 请求开始冻结的snapshot | 旧Trace为null/缺失 |
| effective_config | _runtime_config解析后的实际RetrievalRuntimeConfig | 不读设置页草稿，不补造旧值 |
| llm.called/provider/model | 本请求是否尝试改写或生成、同一snapshot模型身份 | 仅此三字段，无Key/Base URL/secret source |

effective_config覆盖mode、final K、Dense K/threshold、BM25 K/k1/b、RRF k、Reranker开关/K、单文档上限、门控状态。离线retrieve_with_trace可有参数而revision/LLM摘要为null。

called指调用尝试，不是调用成功；先尝试追问改写后空检索仍为true。相同request ID重放使用首次Trace，不套用当前设置。关闭Trace不计算/返回/保存Trace。

## 5. 安全、性能与限制

模型读取/测试结果、校验422均需脱敏；不可将完整snapshot无约束dump到Trace。DPAPI不可用时拒绝持久化密钥，不回退明文。Trace虽不含Key仍含候选全文，需容量限制与访问保护。

拒答门控schema明确拒绝enabled=true，等待[OPT-003 候选深度与拒答门控](../优化/OPT-003-候选深度与拒答门控.md)，没有“隐藏开关可提前启用”。实现[DEV-009 运行时设置与密钥](../开发/DEV-009-运行时设置与密钥.md)、[DEV-010 检索Trace与配置追溯](../开发/DEV-010-检索Trace与配置追溯.md)；验证[TEST-005](../测试/TEST-005-设置与Trace回归.md#cases)。原始实施证据：[S1—S6方案](../../docs/前端设置功能优化方案.md)。

## 关联文档

- 关系：详细实现；[DEV-009 运行时设置与密钥](../开发/DEV-009-运行时设置与密钥.md)
- 关系：详细实现；[DEV-010 检索Trace与配置追溯](../开发/DEV-010-检索Trace与配置追溯.md)
- 关系：关联依赖；[OPT-003 候选深度与拒答门控](../优化/OPT-003-候选深度与拒答门控.md)
- 关系：来源需求；[REQ-004 运行时设置与诊断](../需求/REQ-004-运行时设置与诊断.md)
- 关系：关联依赖；[TEST-005 设置与Trace回归](../测试/TEST-005-设置与Trace回归.md)

## 变更记录

| 日期 | 内容 | 原因 |
|---|---|---|
| 2026-08-27 | 从当前代码与原资料整理，补充证据边界和双向链接 | 建立独立new_docs对照体系，保留原文件 |
