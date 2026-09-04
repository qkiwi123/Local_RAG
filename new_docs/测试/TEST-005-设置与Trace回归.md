# TEST-005 设置与Trace回归

## 文档信息

- 文档ID：TEST-005
- 文档类型：测试
- 状态：测试中
- 创建日期：2026-08-27
- 最后更新：2026-08-27
- 证据口径：当前文件静态核对；历史验证与待验证事项在正文区分，本次未重跑产品测试。
- 导航：[本类目录](./README.md) · [文档中心](../README.md)

## 1. 目标与环境

关系：验证[DEV-009 运行时设置与密钥](../开发/DEV-009-运行时设置与密钥.md)、[DEV-010 检索Trace与配置追溯](../开发/DEV-010-检索Trace与配置追溯.md)及[DEV-008 React主前端](../开发/DEV-008-React主前端.md)设置/Trace交互。使用隔离SQLite、DPAPI替身与mock模型；历史浏览器使用构造Trace，不调用真实问答模型。

<a id="cases"></a>
## 2. 用例

| 用例 | 输入/操作 | 预期 | 代码 | 本次实际 |
|---|---|---|---|---|
| S01 | 保存、旧revision、失败提交 | 快照固定、冲突拒绝、不半提交 | [runtime](../../tests/test_runtime_settings.py) | 未执行 |
| S02 | Key三态、敏感422 | 不回显、不写普通SQLite | [secret](../../tests/test_secret_store.py)、[router](../../tests/test_settings_router.py) | 未执行 |
| S03 | GET状态 | 不冷加载模型 | [router](../../tests/test_settings_router.py) | 未执行 |
| S04 | 草稿测试、reset | 使用草稿、不改.env/索引 | [LLM](../../tests/test_llm_runtime.py)、[设置页](../../web/src/__tests__/settingsPage.test.tsx) | 未执行 |
| S05 | 保存前后chat/重放 | 首次revision/实际参数一致 | [API](../../tests/test_api.py)、[retrieval](../../tests/test_retrieval_runtime.py) | 未执行 |
| S06 | LLM关闭/先改写后空检索 | called准确、无Key | [Trace](../../tests/test_retrieval_trace.py) | 未执行 |
| S07 | 旧Trace缺新字段 | 可读并显示未记录 | [Drawer](../../web/src/__tests__/traceDrawer.test.tsx) | 未执行 |
| S08 | 启用refusal gate | 校准前拒绝 | [runtime](../../tests/test_runtime_settings.py) | 未执行 |

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

## 4. 复跑与外部门禁

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp .pytest-tmp\docs-settings-check tests/test_runtime_settings.py tests/test_settings_router.py tests/test_retrieval_trace.py tests/test_retrieval_runtime.py tests/test_api.py tests/test_chat_stream.py -q
```

真实连接测试需显式允许，dummy Key不能证明认证成功。CPU隔离验收显式关闭RERANKER_USE_FP16，历史曾因继承GPU配置导致启动校验失败。

## 5. 结论与待验收

S1—S6有实现与历史工程验证；S6未提交。S7不在完成范围。多进程热同步、当前真实服务/Key与外部可用性本次未验证。

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

## 变更记录

| 日期 | 内容 | 原因 |
|---|---|---|
| 2026-08-27 | 从当前代码与原资料整理，补充证据边界和双向链接 | 建立独立new_docs对照体系，保留原文件 |
