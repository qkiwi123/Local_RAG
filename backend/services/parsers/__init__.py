"""统一文档格式适配器入口。"""

from backend.services.parsers.constants import PARSER_VERSION
from backend.services.parsers.docx import parse_docx_units
from backend.services.parsers.pdf import parse_pdf_units
from backend.services.parsers.txt import parse_txt_units


ADAPTERS = {
    ".pdf": parse_pdf_units,
    ".docx": parse_docx_units,
    ".txt": parse_txt_units,
}

__all__ = [
    "PARSER_VERSION",
    "ADAPTERS",
    "parse_pdf_units",
    "parse_docx_units",
    "parse_txt_units",
]
