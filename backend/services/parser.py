"""
文档解析器 — 将不同格式的文档统一转为带页码信息的纯文本。

支持的格式：PDF、Word(.docx)、TXT
每种格式的解析策略不同，但输出格式统一为 List[dict]，
每个 dict 包含 text（文本内容）和 page_number（页码）。
"""

from pathlib import Path
from typing import List


def _parse_txt(file_path: str) -> List[dict]:
    """
    解析纯文本文件。

    TXT 文件没有真实分页，按每 50 行标记一个虚拟页码，
    以保证后续引用时有"页码"可供标注。
    """
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    pages = []
    lines_per_page = 50  # 虚拟分页：每 50 行算一页

    for i in range(0, len(lines), lines_per_page):
        chunk_lines = lines[i : i + lines_per_page]
        text = "".join(chunk_lines).strip()
        if text:
            pages.append({
                "text": text,
                "page_number": (i // lines_per_page) + 1  # 页码从 1 开始
            })

    return pages


def _parse_pdf(file_path: str) -> List[dict]:
    """
    解析 PDF 文件，逐页提取文本。

    PyPDF2 的页码从 0 开始，存储时 +1 对齐人类习惯。
    如果某一页无法提取到文本（扫描件/图片型 PDF），
    标记为 [第X页无可提取文本] 以便系统知晓。
    """
    from PyPDF2 import PdfReader

    reader = PdfReader(file_path)
    pages = []

    for i, page in enumerate(reader.pages):
        text = page.extract_text()

        # 某些 PDF 页面可能没有可提取的文本层（如图片型 PDF）
        if not text or not text.strip():
            text = f"[第{i + 1}页无可提取文本]"
        else:
            text = text.strip()

        pages.append({
            "text": text,
            "page_number": i + 1  # PyPDF2 的页码从 0 开始，+1 对齐人类习惯
        })

    return pages


def _parse_docx(file_path: str) -> List[dict]:
    """
    解析 Word (.docx) 文件。

    Word 没有原生的"页"概念，按每 30 个段落标记一个虚拟页码。
    这是折中方案——真实页码依赖于渲染引擎（Word 的分页受字体、
    页边距等影响），无法在纯文本解析阶段精确还原。
    """
    from docx import Document

    doc = Document(file_path)
    paragraphs = [p.text for p in doc.paragraphs]

    pages = []
    paras_per_page = 30  # 虚拟分页：每 30 个段落算一页

    for i in range(0, len(paragraphs), paras_per_page):
        chunk_paras = paragraphs[i : i + paras_per_page]
        text = "\n".join(p for p in chunk_paras if p.strip())
        if text.strip():
            pages.append({
                "text": text,
                "page_number": (i // paras_per_page) + 1
            })

    return pages


# 文件后缀 → 解析函数的映射表
_PARSER_MAP = {
    ".pdf": _parse_pdf,
    ".docx": _parse_docx,
    ".txt": _parse_txt,
}


def parse_document(
    file_path: str,
    document_name: str = None,
    document_id: str = None,
) -> List[dict]:
    """
    统一入口：根据文件后缀名分发到对应的解析函数。

    Args:
        file_path: 文档的绝对或相对路径
        document_name: 文档名（可选，默认从路径中提取文件名）
        document_id: 稳定文档 ID（可选；缺省时使用文档名，便于评测可复现）

    Returns:
        List[dict]，每个元素包含:
            - text: 该页的纯文本
            - page_number: 页码（从 1 开始）
            - document_name: 文档文件名
    """
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix not in _PARSER_MAP:
        raise ValueError(f"不支持的文件格式: {suffix}（当前支持: .pdf, .docx, .txt）")

    if document_name is None:
        document_name = path.name
    if document_id is None:
        document_id = document_name

    parse_func = _PARSER_MAP[suffix]
    pages = parse_func(file_path)

    # 将文档名注入每一页的结果中，方便后续 chunker 使用
    for page in pages:
        page["document_name"] = document_name
        page["document_id"] = document_id

    return pages
