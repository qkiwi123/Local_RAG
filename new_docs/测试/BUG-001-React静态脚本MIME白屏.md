# BUG-001 React静态脚本MIME白屏

## 文档信息

- 文档ID：BUG-001
- 文档类型：Bug
- 状态：已完成
- 创建日期：2026-08-27
- 最后更新：2026-08-27
- 证据口径：当前文件静态核对；历史验证与待验证事项在正文区分，本次未重跑产品测试。
- 导航：[本类目录](./README.md) · [文档中心](../README.md)

## 1. 问题与发现位置

旧[测试反馈第20条](../../docs/测试反馈记录.md)记录：FastAPI根路径和JS均返回200，但React根节点为空。发现与追踪入口[反馈表](../测试/TEST-007-缺陷与历史反馈追踪.md#feedback)。

## 2. 根因与影响

Windows MIME注册表可能把.js映射为text/plain；浏览器拒绝执行ES module。影响FastAPI托管的React主界面，不等于API服务宕机。

<a id="fix"></a>
## 3. 修复位置与实现

关系：修复由[DEV-012](../开发/DEV-012-服务托管与启动停止.md#bug-fixes)承接。[backend/main.py](../../backend/main.py)创建静态响应前使用mimetypes.add_type注册text/javascript。小修复记录在原模块，不虚构新架构方案。

<a id="regression"></a>
## 4. 回归与状态

[test_spa_static.py](../../tests/test_spa_static.py)的test_javascript_asset_uses_executable_mime_type检查实际JS Content-Type，而非只检查HTTP状态。历史记录确认text/javascript响应与真实浏览器挂载。

修复状态：历史已修复，当前修复代码与测试仍存在；本次未重跑。验证由[TEST-009 O02](../测试/TEST-009-本地运行与回滚验证.md#cases)维护。若复发，先查实际响应头、构建缓存和浏览器错误，不能仅凭200关闭问题。

## 关联文档

- 关系：修复实现；[DEV-012 服务托管与启动停止](../开发/DEV-012-服务托管与启动停止.md)
- 关系：关联引用；[OPS-003 设置密钥与故障排查](../运维/OPS-003-设置密钥与故障排查.md)
- 关系：关联引用；[TEST-004 标签与React交互](../测试/TEST-004-标签与React交互.md)
- 关系：发现问题记录；[TEST-007 缺陷与历史反馈追踪](../测试/TEST-007-缺陷与历史反馈追踪.md)
- 关系：回归验证；[TEST-009 本地运行与回滚验证](../测试/TEST-009-本地运行与回滚验证.md)

## 变更记录

| 日期 | 内容 | 原因 |
|---|---|---|
| 2026-08-27 | 从当前代码与原资料整理，补充证据边界和双向链接 | 建立独立new_docs对照体系，保留原文件 |
