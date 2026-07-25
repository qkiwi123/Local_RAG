"""结构化 adapter 共用的确定性表格文本表示。"""

from __future__ import annotations

from backend.models.document_structure import render_table_text


def render_table_markdown(rows: list[list[str]]) -> str:
    return render_table_text(rows)
