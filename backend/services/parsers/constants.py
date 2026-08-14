"""统一结构化 parser 的版本常量。

v2：新增可选 OCR/图片理解——视觉产出进入 canonical text 并带 ocr 属性标记；
OCR 全部关闭时输出与 structured-v1 完全一致（版本仍全局升级，避免混写）。
"""

PARSER_VERSION = "structured-v2"
