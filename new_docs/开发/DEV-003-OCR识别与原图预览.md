# DEV-003 OCR识别与原图预览

## 文档信息

- 文档ID：DEV-003
- 文档类型：开发
- 状态：已完成
- 创建日期：2026-08-27
- 最后更新：2026-08-27
- 证据口径：当前文件静态核对；历史验证与待验证事项在正文区分，本次未重跑产品测试。
- 导航：[本类目录](./README.md) · [文档中心](../README.md)

## 1. 来源设计与职责

关系：实现[OCR分层设计](../设计/DES-002-结构化解析与OCR.md#ocr)。负责视觉调用、缓存、warning、确定性结构分类与原图预览，不改变聊天LLM或把OCR输出当人工可信事实。

## 2. 代码索引

| 代码 | 职责 |
|---|---|
| [ocr.py](../../backend/services/parsers/ocr.py) | A/B/C层、图片预算与失败记忆 |
| [vision.py](../../backend/services/vision.py) | 可配置视觉接口 |
| [vision_cache.py](../../backend/services/vision_cache.py) | 成功文本磁盘缓存 |
| [ocr_structure.py](../../backend/services/parsers/ocr_structure.py) | 不改写文本的标题/列表分类 |
| [ocr_image_store.py](../../backend/services/ocr_image_store.py) | 原图保存、引用与路径限制 |
| [StructuredRenderer.tsx](../../web/src/components/StructuredRenderer.tsx) | 结构和原图对照展示 |

<a id="implementation"></a>
## 3. 核心流程

解析器发现符合开关的图片/扫描页 → 检查图片格式与预算 → 查询缓存 → 必要时调用视觉服务 → 文本以attributes.ocr进入结构 → 可选保存原图并记录ocr_image_ref → 前端按安全接口取图。

视觉模型默认配置为glm-4.6v-flash，这是仓库默认，不是本次对外部模型可用性的验证。A/B/C层默认关闭；OCR_IMAGE_PREVIEW_ENABLED默认开启，但它本身不会发起视觉调用。

缓存命中不扣真实调用预算；失败记录在进程内短期抑制重复尝试。缓存写失败不应阻断入库；OCR警告应在上传结果与前端可见。

<a id="image"></a>
## 4. 原图、异常与已知边界

GET /api/documents/id/{document_id}/ocr-images/{image_ref}为只读图像接口，需验证文档归属和路径。历史资料没有保存原图时，不凭已有OCR文字补造图片。

EMF等不可处理格式跳过并warning；识别失败不等于整个有文本文件必须失败。零有效文本仍可导致入库失败。OCR表格目前不是完整结构化表格恢复，后续视觉缓存淘汰也尚未实现。

## 5. 实际结果与验证

结构分类、图片存储/接口及React渲染代码已存在；本次未调用视觉API、未重传扫描件。历史验收来源：[2026-08-16记录](../../docs/superpowers/plans/2026-08-16-v2-acceptance-round-plan.md)。测试见[OCR用例](../测试/TEST-001-解析入库与OCR.md#ocr)，容量风险见[OPT-004 视觉缓存容量治理](../优化/OPT-004-视觉缓存容量治理.md)；失败重试与安全历史见[TEST-007 缺陷与历史反馈追踪](../测试/TEST-007-缺陷与历史反馈追踪.md)。

## 关联文档

- 关系：来源设计；[DES-002 结构化解析与OCR](../设计/DES-002-结构化解析与OCR.md)
- 关系：运维；[OPS-002 数据备份恢复与索引重建](../运维/OPS-002-数据备份恢复与索引重建.md)
- 关系：关联引用；[OPS-003 设置密钥与故障排查](../运维/OPS-003-设置密钥与故障排查.md)
- 关系：优化；[OPT-004 视觉缓存容量治理](../优化/OPT-004-视觉缓存容量治理.md)
- 关系：测试验证；[TEST-001 解析入库与OCR](../测试/TEST-001-解析入库与OCR.md)
- 关系：测试验证；[TEST-007 缺陷与历史反馈追踪](../测试/TEST-007-缺陷与历史反馈追踪.md)

## 变更记录

| 日期 | 内容 | 原因 |
|---|---|---|
| 2026-08-27 | 从当前代码与原资料整理，补充证据边界和双向链接 | 建立独立new_docs对照体系，保留原文件 |
