# TEST-004 标签与React交互

## 文档信息

- 文档ID：TEST-004
- 文档类型：测试
- 状态：测试中
- 创建日期：2026-08-27
- 最后更新：2026-08-27
- 证据口径：当前文件静态核对；历史验证与待验证事项在正文区分，本次未重跑产品测试。
- 导航：[本类目录](./README.md) · [文档中心](../README.md)

## 1. 目标与数据

关系：验证[DEV-007 标签与知识库管理](../开发/DEV-007-标签与知识库管理.md)、[DEV-008 React主前端](../开发/DEV-008-React主前端.md)与[交互要求](../需求/REQ-003-会话与知识库交互.md#acceptance)。单测用mock HTTP和隔离标签存储；布局需真实浏览器。

<a id="cases"></a>
## 2. 用例

| 用例 | 输入/操作 | 预期 | 代码/证据 | 本次实际 |
|---|---|---|---|---|
| U01 | 空标签、父级、多选 | 不挂文档；含子孙/并集 | [tag_store](../../tests/test_tag_store.py)、[tagTree](../../web/src/__tests__/tagTree.test.ts) | 未执行 |
| U02 | q+tags | 名称子串与范围交集 | [API](../../tests/test_api.py)、[knowledgePage](../../web/src/__tests__/knowledgePage.test.tsx) | 未执行 |
| U03 | 选文件、关闭抽屉、重试 | 暂存队列保留，不重传成功项 | [knowledgePage](../../web/src/__tests__/knowledgePage.test.tsx) | 未执行 |
| U04 | 发送、409、停止/重试 | 即时问题、同步不重发、复用ID | [chatPage](../../web/src/__tests__/chatPage.test.tsx) | 未执行 |
| U05 | 桌面缩放、窄屏、调宽 | 无整页横溢/遮挡 | [UX验收](../../docs/superpowers/plans/2026-08-15-phase-3-frontend-ux-redesign-plan.md) | 未执行 |
| U06 | OCR图像、warning | 安全路径与风险提示 | [ocrImageUrl](../../web/src/__tests__/ocrImageUrl.test.ts) | 未执行 |

## 3. 历史结果

2026-08-15 F4记录前端27/27、后端461 passed/3 skipped；浏览器覆盖1920×1080、1536×864、1280×720、1024×768、768×1024、390×844。来源：[UX记录](../../docs/superpowers/plans/2026-08-15-phase-3-frontend-ux-redesign-plan.md)。后续S6的39项前端结果见[TEST-005 设置与Trace回归](../测试/TEST-005-设置与Trace回归.md)；不能跨日期叠加成当前总数。

## 4. 复跑命令

在web目录执行：

```powershell
npm.cmd test -- --reporter=dot
npm.cmd run lint
npm.cmd run build
```

构建会更新web/dist；本次未执行。手测前选择隔离环境并确认模型开关和端口归属。

## 5. Bug与结论

反馈21曾在没有完整几何检查时误报完成，后续重构才形成多视口证据；见[反馈21—24](../测试/TEST-007-缺陷与历史反馈追踪.md#feedback)。MIME白屏见[BUG-001 React静态脚本MIME白屏](../测试/BUG-001-React静态脚本MIME白屏.md)。jsdom不替代字体、缩放和真实DOM挂载；当前没有新版体积优化对照。

## 关联文档

- 关系：关联依赖；[BUG-001 React静态脚本MIME白屏](../测试/BUG-001-React静态脚本MIME白屏.md)
- 关系：关联引用；[DES-005 标签与React交互](../设计/DES-005-标签与React交互.md)
- 关系：验证对象；[DEV-007 标签与知识库管理](../开发/DEV-007-标签与知识库管理.md)
- 关系：验证对象；[DEV-008 React主前端](../开发/DEV-008-React主前端.md)
- 关系：关联引用；[REQ-003 会话与知识库交互](../需求/REQ-003-会话与知识库交互.md)
- 关系：关联依赖；[TEST-005 设置与Trace回归](../测试/TEST-005-设置与Trace回归.md)
- 关系：关联依赖；[TEST-007 缺陷与历史反馈追踪](../测试/TEST-007-缺陷与历史反馈追踪.md)

## 变更记录

| 日期 | 内容 | 原因 |
|---|---|---|
| 2026-08-27 | 从当前代码与原资料整理，补充证据边界和双向链接 | 建立独立new_docs对照体系，保留原文件 |
