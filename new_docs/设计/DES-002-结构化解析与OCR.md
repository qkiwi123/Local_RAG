# DES-002 结构化解析与OCR

## 文档信息

- 文档ID：DES-002
- 文档类型：设计
- 状态：已完成
- 创建日期：2026-08-27
- 最后更新：2026-08-27
- 证据口径：当前文件静态核对；历史验证与待验证事项在正文区分，本次未重跑产品测试。
- 导航：[本类目录](./README.md) · [文档中心](../README.md)

## 1. 对应需求与目标

关系：实现[REQ-001功能需求](../需求/REQ-001-文档入库与可定位来源.md#requirements)。解决多格式结构丢失、来源位置不统一，以及视觉文本不能定位的问题。

<a id="contract"></a>
## 2. 统一数据模型

[document_structure.py](../../backend/models/document_structure.py)定义ParsedBlock、ParsedUnit、SourceLocator等模型。Adapter负责格式内容；Assembler统一生成稳定ID、连续sequence、canonical text与字符offset；chunker消费统一unit，不重新发明定位。

| 格式 | locator_kind | 结构重点 |
|---|---|---|
| PDF | page | 真实页面、段落；必要时OCR |
| DOCX | virtual_page | 标题路径、段落、列表、表格 |
| TXT | virtual_page | 物理行窗口与段落 |
| Markdown | section | 标题、列表、引用、代码块 |
| PPTX | slide | 幻灯片、文本框、组合形状和表格 |

枚举中预留sheet不等于Excel已实现；注册表只有五种格式。unit_id、parser_version、block信息和offset共同定位；前端显示location_label，不统一称为页码。

## 3. 核心流程与接口

文件 → parse_document_structure → ParsedDocument(units, warnings) → chunk_units → BGE编码 → Chroma；完整unit存SQLite供预览。入口见[parsers/__init__.py](../../backend/services/parsers/__init__.py)。

结构读取重新做模型校验，损坏JSON或不兼容版本应报告错误。PDF主解析失败可降级到pdfium，仍失败才终止。

<a id="ocr"></a>
## 4. OCR分层、降级和成本

A层扫描PDF整页；B层PDF含图页面；C层DOCX/PPTX内嵌图片。总开关与分层开关默认关闭；VISION配置只影响入库视觉服务，不替代问答LLM。

成功识别进入canonical text，标attributes.ocr；失败产生warning。缓存复用不消耗单文档真实调用预算；失败有进程内短期记忆。文本结构分类与原图预览是展示增强，不应悄悄改写原文字串或触发全库重建。

关系：详细实现[DEV-003](../开发/DEV-003-OCR识别与原图预览.md#implementation)，验证[TEST-001 OCR场景](../测试/TEST-001-解析入库与OCR.md#ocr)。

## 5. 选型、取舍与限制

确定性结构模型比每种格式独立检索协议更容易统一引用；但逻辑页并非真实排版。OCR不保证识别准确；表格OCR转结构化Markdown尚未完成。DOCX/PPTX不支持完整视觉版面、SmartArt或演讲者备注；Markdown外部图片不自动下载。

文档/模型/parser/切分变更可能改变chunk身份，需[独立重建与验收](../运维/OPS-002-数据备份恢复与索引重建.md#rebuild)，不能只改版本字符串。开发拆分：[DEV-001 五格式结构化解析](../开发/DEV-001-五格式结构化解析.md)、[DEV-002 上传与索引生命周期](../开发/DEV-002-上传与索引生命周期.md)、[DEV-003 OCR识别与原图预览](../开发/DEV-003-OCR识别与原图预览.md)、[DEV-006 来源引用与结构预览](../开发/DEV-006-来源引用与结构预览.md)。

## 关联文档

- 关系：详细实现；[DEV-001 五格式结构化解析](../开发/DEV-001-五格式结构化解析.md)
- 关系：详细实现；[DEV-002 上传与索引生命周期](../开发/DEV-002-上传与索引生命周期.md)
- 关系：详细实现；[DEV-003 OCR识别与原图预览](../开发/DEV-003-OCR识别与原图预览.md)
- 关系：详细实现；[DEV-006 来源引用与结构预览](../开发/DEV-006-来源引用与结构预览.md)
- 关系：关联依赖；[OPS-002 数据备份恢复与索引重建](../运维/OPS-002-数据备份恢复与索引重建.md)
- 关系：来源需求；[REQ-001 文档入库与可定位来源](../需求/REQ-001-文档入库与可定位来源.md)
- 关系：关联依赖；[TEST-001 解析入库与OCR](../测试/TEST-001-解析入库与OCR.md)

## 变更记录

| 日期 | 内容 | 原因 |
|---|---|---|
| 2026-08-27 | 从当前代码与原资料整理，补充证据边界和双向链接 | 建立独立new_docs对照体系，保留原文件 |
