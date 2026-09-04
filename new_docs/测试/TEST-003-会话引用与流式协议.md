# TEST-003 会话引用与流式协议

## 文档信息

- 文档ID：TEST-003
- 文档类型：测试
- 状态：测试中
- 创建日期：2026-08-27
- 最后更新：2026-08-27
- 证据口径：当前文件静态核对；历史验证与待验证事项在正文区分，本次未重跑产品测试。
- 导航：[本类目录](./README.md) · [文档中心](../README.md)

## 1. 目标与环境

关系：验证[DEV-005 会话流式问答](../开发/DEV-005-会话流式问答.md)、[DEV-006 来源引用与结构预览](../开发/DEV-006-来源引用与结构预览.md)及[NDJSON协议](../设计/DES-004-会话流式协议与来源预览.md#stream)。使用TestClient、隔离SQLite、mock LLM和结构fixture；mock通过不代表真实回答/网络质量。

<a id="cases"></a>
## 2. 用例

| 用例 | 输入 | 预期 | 证据 | 本次实际 |
|---|---|---|---|---|
| C01 | 单次/持久化chat | 答案、引用保存 | [API](../../tests/test_api.py)、[会话](../../tests/test_conversation_store.py) | 未执行 |
| C02 | 相同request_id、旧末尾 | 重放或409，不重复生成 | [会话测试](../../tests/test_conversation_store.py) | 未执行 |
| C03 | 分块跨JSON行 | 事件顺序及拼接正确 | [后端](../../tests/test_chat_stream.py)、[前端](../../web/src/__tests__/stream.test.ts) | 未执行 |
| C04 | 检索/生成失败、断开 | error/interrupted，无半截完成答案 | [stream测试](../../tests/test_chat_stream.py) | 未执行 |
| C05 | 无标记/越界编号 | 不兜底造引用 | [citation测试](../../tests/test_citation.py) | 未执行 |
| C06 | 跨格式、跨unit、失效chunk | 正确定位或明确错误 | [结构预览](../../tests/test_structured_citation_preview.py) | 未执行 |

## 3. 复跑

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp .pytest-tmp\docs-chat-check tests/test_conversation_store.py tests/test_chat_stream.py tests/test_citation.py tests/test_structured_citation_preview.py -q
```

真实浏览器还需验证停止后刷新、服务端先完成时Abort、409同步及失败重试；仅断开客户端请求不能证明服务端未落库。

## 4. 历史结果、Bug与结论

S6历史定向83项包含chat/API/会话路径，来源[S6证据](../测试/TEST-005-设置与Trace回归.md#evidence)；不拆造独立通过数。反馈17为重同步、22为即时用户消息，见[历史反馈](../测试/TEST-007-缺陷与历史反馈追踪.md#feedback)。

引用指标版本误判属于[BUG-002 结构化引用指标版本误判](../测试/BUG-002-结构化引用指标版本误判.md)，不是预览接口修复。本次没有新增性能或外部模型测试；结论为覆盖已定位、待按风险复跑。

## 关联文档

- 关系：关联依赖；[BUG-002 结构化引用指标版本误判](../测试/BUG-002-结构化引用指标版本误判.md)
- 关系：关联引用；[DES-004 会话流式协议与来源预览](../设计/DES-004-会话流式协议与来源预览.md)
- 关系：验证对象；[DEV-005 会话流式问答](../开发/DEV-005-会话流式问答.md)
- 关系：验证对象；[DEV-006 来源引用与结构预览](../开发/DEV-006-来源引用与结构预览.md)
- 关系：关联引用；[REQ-003 会话与知识库交互](../需求/REQ-003-会话与知识库交互.md)
- 关系：关联依赖；[TEST-005 设置与Trace回归](../测试/TEST-005-设置与Trace回归.md)
- 关系：关联依赖；[TEST-007 缺陷与历史反馈追踪](../测试/TEST-007-缺陷与历史反馈追踪.md)

## 变更记录

| 日期 | 内容 | 原因 |
|---|---|---|
| 2026-08-27 | 从当前代码与原资料整理，补充证据边界和双向链接 | 建立独立new_docs对照体系，保留原文件 |
