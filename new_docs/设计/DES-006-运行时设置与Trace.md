# DES-006 运行时设置与Trace

## 文档信息

- 文档ID：DES-006
- 文档类型：设计
- 状态：已完成
- 创建日期：2026-08-27
- 最后更新：2026-09-09
- 证据口径：当前文件静态核对，并纳入2026-09-09对同步/流式计时边界的代码核对与全量自动化回归；BUG-004传输修复已验证，Trace计量拆分尚未实施。
- 完成范围：本文已完成设置快照、Trace安全和旧Trace兼容的设计合同；新增阶段计时字段和前端显示修订仍属OPT-006规划。
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

<a id="timing"></a>
## 5. Trace计时合同与当前缺口（2026-09-09）

- 当前`RetrievalTraceTimings`只有检索、上下文组装、LLM生成和总耗时八个字段；流式有结果路径暂未填写真实的`context_formatting`与`llm_generation`，前端的“其他阶段”只是`total`减已知字段的差值，不能当作后端实测阶段。
- 同步接口的`total`从路由入口开始，流式接口的`total`在生成器内、`meta`发送前开始；两者都在Trace完成、持久化之前结束。流式追问改写发生在该计时起点之前，持久化发生在该计时终点之后，当前数据不能用一个差值同时归因这些区段。
- 未来新增改写、引用提取、持久化、服务端首个delta和delta窗口字段时，未执行路径使用`null`或缺失表达“不适用/未记录”，不能用0伪造测量；旧Trace不迁移、不回填。字段命名、总耗时边界和前端显示由[OPT-006](../优化/OPT-006-Trace计量拆分与首字延迟优化.md#plan)统一门禁。
- 服务端首个delta时间与浏览器实际收到首个delta时间分开记录；Trace只保留必要的诊断摘要，不得写入API Key、授权头、Base URL或异常明文。

<a id="limitations"></a>
## 6. 安全、性能与限制

模型读取/测试结果、校验422均需脱敏；不可将完整snapshot无约束dump到Trace。DPAPI不可用时拒绝持久化密钥，不回退明文。Trace虽不含Key仍含候选全文，需容量限制与访问保护。

拒答门控schema明确拒绝enabled=true，等待[OPT-003 候选深度与拒答门控](../优化/OPT-003-候选深度与拒答门控.md)，没有“隐藏开关可提前启用”。实现[DEV-009 运行时设置与密钥](../开发/DEV-009-运行时设置与密钥.md)、[DEV-010 检索Trace与配置追溯](../开发/DEV-010-检索Trace与配置追溯.md)；验证[TEST-005](../测试/TEST-005-设置与Trace回归.md#cases)。原始实施证据：[S1—S6方案](../../docs/前端设置功能优化方案.md)。

## 关联文档

- 关系：详细实现；[DEV-009 运行时设置与密钥](../开发/DEV-009-运行时设置与密钥.md)
- 关系：详细实现；[DEV-010 检索Trace与配置追溯](../开发/DEV-010-检索Trace与配置追溯.md)
- 关系：关联依赖；[OPT-003 候选深度与拒答门控](../优化/OPT-003-候选深度与拒答门控.md)
- 关系：来源需求；[REQ-004 运行时设置与诊断](../需求/REQ-004-运行时设置与诊断.md)
- 关系：关联依赖；[TEST-005 设置与Trace回归](../测试/TEST-005-设置与Trace回归.md)
- 关系：后续优化；[OPT-006 Trace计量拆分与首字延迟优化](../优化/OPT-006-Trace计量拆分与首字延迟优化.md#plan)

## 变更记录

| 日期 | 内容 | 原因 |
|---|---|---|
| 2026-09-09 | 同步BUG-004全量及真实本地TCP回归结果，明确它未新增Trace计量字段 | 区分传输链路修复证据与计量合同后续实现 |
| 2026-09-09 | 补充同步/流式总耗时边界、未执行字段语义、前端差值限制和服务端/客户端首字时间区分 | 将Trace展示从“能读配置”扩展为可审计的计量合同，并避免把未测差值写成阶段耗时 |
| 2026-08-27 | 从当前代码与原资料整理，补充证据边界和双向链接 | 建立独立new_docs对照体系，保留原文件 |
