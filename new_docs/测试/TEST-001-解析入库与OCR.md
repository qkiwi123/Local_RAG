# TEST-001 解析入库与OCR

## 文档信息

- 文档ID：TEST-001
- 文档类型：测试
- 状态：测试中
- 创建日期：2026-08-27
- 最后更新：2026-08-27
- 证据口径：当前文件静态核对；历史验证与待验证事项在正文区分，本次未重跑产品测试。
- 导航：[本类目录](./README.md) · [文档中心](../README.md)

## 1. 测试目标与对象

关系：验证[DEV-001 五格式结构化解析](../开发/DEV-001-五格式结构化解析.md)、[DEV-002 上传与索引生命周期](../开发/DEV-002-上传与索引生命周期.md)、[DEV-003 OCR识别与原图预览](../开发/DEV-003-OCR识别与原图预览.md)，对应[入库验收条件](../需求/REQ-001-文档入库与可定位来源.md#acceptance)。覆盖解析、结构不变量、入库补偿、OCR降级和图像安全。

## 2. 环境、数据与方法

使用项目.venv、pytest临时目录与tests/fixtures。OCR用mock隔离，本地模型集成另分组；tests/conftest.py注入隔离metadata/Chroma/uploads、关闭OCR和预热，并按标记跳过外部测试。

<a id="cases"></a>
## 3. 关键用例

| 用例 | 输入/方法 | 预期 | 代码证据 | 本次实际 |
|---|---|---|---|---|
| P01 | 五格式fixture | locator与结构正确 | [注册测试](../../tests/test_parser_registry.py)及各Adapter测试 | 未执行 |
| P02 | unit重复组装/切分 | ID稳定、offset切片匹配 | [结构](../../tests/test_document_structure.py)、[chunker](../../tests/test_chunker.py) | 未执行 |
| P03 | 同名、非法路径、超限 | ID独立、拒绝路径、413 | [API](../../tests/test_api.py)、[入库](../../tests/test_structured_ingest.py) | 未执行 |
| P04 | 入库中途失败 | 清理派生数据、保留失败诊断 | [入库测试](../../tests/test_structured_ingest.py) | 未执行 |
| P05 | 重建目标非空、缺原文件 | 拒绝不安全重建 | [rebuild测试](../../tests/test_rebuild_index.py) | 未执行 |
| P06 | 损坏PDF | 备用引擎或明确失败 | [PDF测试](../../tests/test_pdf_adapter.py) | 未执行 |

<a id="ocr"></a>
## 4. OCR验证

| 用例 | 输入 | 预期 | 证据 | 本次实际 |
|---|---|---|---|---|
| O01 | 关闭/视觉失败 | 零调用或warning降级 | [test_parser_ocr.py](../../tests/test_parser_ocr.py) | 未执行 |
| O02 | 重复图片、预算耗尽 | 缓存复用、限制真实调用 | [test_vision_cache.py](../../tests/test_vision_cache.py) | 未执行 |
| O03 | 标题/列表/混合段落 | 文本不改写 | [test_ocr_structure.py](../../tests/test_ocr_structure.py) | 未执行 |
| O04 | 非法image_ref/跨文档取图 | 拒绝越界 | [test_ocr_image_endpoint.py](../../tests/test_ocr_image_endpoint.py) | 未执行 |
| O05 | 真实扫描/图表 | 人工确认识别质量 | 需外部API与人工验收 | 待确认 |

## 5. 复跑命令与结果口径

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp .pytest-tmp\docs-ingest-check tests/test_document_structure.py tests/test_parser_registry.py tests/test_structured_ingest.py tests/test_pdf_adapter.py tests/test_docx_adapter.py tests/test_txt_adapter.py tests/test_markdown_adapter.py tests/test_pptx_adapter.py tests/test_parser_ocr.py tests/test_ocr_structure.py tests/test_ocr_image_endpoint.py tests/test_rebuild_index.py -q
```

命令未在本次执行。2026-08-16已有OCR结构/原图验收记录：[原记录](../../docs/superpowers/plans/2026-08-16-v2-acceptance-round-plan.md)。历史整套通过不能替每行补造独立测试结果。

## 6. Bug、性能与结论

历史PDF、OCR失败风暴见[反馈8/13/14/15](../测试/TEST-007-缺陷与历史反馈追踪.md#feedback)。没有当前视觉缓存容量压测，跟进[OPT-004 视觉缓存容量治理](../优化/OPT-004-视觉缓存容量治理.md)。覆盖入口已映射，真实外部识别和全量回归需另行执行。

## 关联文档

- 关系：关联引用；[DES-002 结构化解析与OCR](../设计/DES-002-结构化解析与OCR.md)
- 关系：验证对象；[DEV-001 五格式结构化解析](../开发/DEV-001-五格式结构化解析.md)
- 关系：验证对象；[DEV-002 上传与索引生命周期](../开发/DEV-002-上传与索引生命周期.md)
- 关系：验证对象；[DEV-003 OCR识别与原图预览](../开发/DEV-003-OCR识别与原图预览.md)
- 关系：优化跟进；[OPT-004 视觉缓存容量治理](../优化/OPT-004-视觉缓存容量治理.md)
- 关系：关联引用；[REQ-001 文档入库与可定位来源](../需求/REQ-001-文档入库与可定位来源.md)
- 关系：关联依赖；[TEST-007 缺陷与历史反馈追踪](../测试/TEST-007-缺陷与历史反馈追踪.md)

## 变更记录

| 日期 | 内容 | 原因 |
|---|---|---|
| 2026-08-27 | 从当前代码与原资料整理，补充证据边界和双向链接 | 建立独立new_docs对照体系，保留原文件 |
