"""统一文档格式适配器。

所有 adapter 都输出 ``list[ParsedUnit]``，不向下游暴露格式专用 dict。
"""

PARSER_VERSION = "structured-v1"

from backend.services.parsers.docx import parse_docx_units
from backend.services.parsers.pdf import parse_pdf_units

__all__ = ["PARSER_VERSION", "parse_pdf_units", "parse_docx_units"]
