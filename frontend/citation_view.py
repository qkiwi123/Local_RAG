"""Streamlit 引用原文预览组件。

提供：
- build_highlight_html: 安全 HTML 转义后精确包裹 <mark>
- fetch_citation_preview: lazy-load 预览数据
- render_citations: 统一引用列表 + 按需预览对话框
"""

import html
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from backend.models.document_structure import LocatedBlock, render_table_text


PREVIEW_DIALOG_BEHAVIOR_HTML = """
<script>
(() => {
  const hostWindow = window.parent;
  const hostDocument = hostWindow.document;

  function configurePreviewDialog() {
    const dialogs = hostDocument.querySelectorAll('[data-testid="stDialog"]');
    const dialog = dialogs[dialogs.length - 1];
    if (!dialog) {
      return false;
    }

    const closeButton = dialog.querySelector('button[aria-label="Close"]');
    const panel = dialog.querySelector('[role="dialog"]');
    if (closeButton) {
      const panelRect = panel ? panel.getBoundingClientRect() : null;
      const surface = panel?.parentElement || panel;
      const surfaceStyle = surface
        ? hostWindow.getComputedStyle(surface)
        : null;
      const right = panelRect
        ? Math.max(16, hostWindow.innerWidth - panelRect.right + 16)
        : 16;
      const backgroundColor = surfaceStyle?.backgroundColor || 'Canvas';
      const textColor = surfaceStyle?.color || 'CanvasText';
      closeButton.style.setProperty('position', 'fixed', 'important');
      closeButton.style.setProperty('top', '4rem', 'important');
      closeButton.style.setProperty('right', `${right}px`, 'important');
      closeButton.style.setProperty('width', '2.25rem', 'important');
      closeButton.style.setProperty('height', '2.25rem', 'important');
      closeButton.style.setProperty('z-index', '1000001', 'important');
      closeButton.style.setProperty('background', backgroundColor, 'important');
      closeButton.style.setProperty('color', textColor, 'important');
      closeButton.style.setProperty(
        'border',
        '1px solid color-mix(in srgb, currentColor 24%, transparent)',
        'important'
      );
      closeButton.style.setProperty(
        'box-shadow',
        '0 2px 8px rgba(0, 0, 0, 0.24)',
        'important'
      );
      closeButton.title = '关闭预览';
    }

    const target = dialog.querySelector(
      '[data-citation-preview-target="true"] mark'
    );
    if (!target) {
      return false;
    }

    const dialogRect = dialog.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    const centeredTop = (
      dialog.scrollTop
      + targetRect.top
      - dialogRect.top
      - (dialog.clientHeight / 2)
      + (targetRect.height / 2)
    );
    dialog.scrollTo({top: Math.max(0, centeredTop), behavior: 'auto'});
    return true;
  }

  let attempts = 0;
  const timer = hostWindow.setInterval(() => {
    attempts += 1;
    if (configurePreviewDialog() || attempts >= 20) {
      hostWindow.clearInterval(timer);
    }
  }, 50);
})();
</script>
"""


def render_preview_dialog_behavior() -> None:
    """固定关闭按钮，并在弹窗首次渲染时将引用高亮滚动到视口中央。"""
    components.html(PREVIEW_DIALOG_BEHAVIOR_HTML, height=0)


def build_highlight_html(text: str, start: int, end: int) -> str:
    """将 text 中 [start, end) 区间用 <mark> 包裹，其余全部 HTML 转义。"""
    if not text:
        raise ValueError("text 不能为空")
    if start < 0:
        raise ValueError("start 不能为负数")
    if end > len(text):
        raise ValueError(f"end={end} 超出文本长度 {len(text)}")
    if end <= start:
        raise ValueError("end 必须大于 start")

    before = html.escape(text[:start])
    highlighted = html.escape(text[start:end])
    after = html.escape(text[end:])

    return (
        f'<pre style="white-space: pre-wrap; font-family: inherit;">'
        f"{before}<mark>{highlighted}</mark>{after}"
        f"</pre>"
    )


def _escaped_with_highlight(
    text: str,
    absolute_start: int,
    highlight_start: int,
    highlight_end: int,
) -> str:
    output: list[str] = []
    for index, character in enumerate(text):
        position = absolute_start + index
        escaped = "<br>" if character == "\n" else html.escape(character)
        if highlight_start <= position < highlight_end:
            output.append(f"<mark>{escaped}</mark>")
        else:
            output.append(escaped)
    return "".join(output)


def _table_html(
    block: LocatedBlock,
    highlight_start: int,
    highlight_end: int,
) -> str:
    rows = block.table_rows or []
    if render_table_text(rows) != block.text:
        raise ValueError("table_rows 与规范表格文本不一致")
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]

    line_starts = [0]
    for line in block.text.split("\n")[:-1]:
        line_starts.append(line_starts[-1] + len(line) + 1)
    displayed_line_indexes = [0, *range(2, len(padded) + 1)]

    output = ["<table>"]
    for row_index, row in enumerate(padded):
        tag = "th" if row_index == 0 else "td"
        output.append("<tr>")
        line_start = line_starts[displayed_line_indexes[row_index]]
        cell_start = line_start + 2
        for cell in row:
            rendered_cell = cell.replace("|", r"\|").replace("\n", "<br>")
            # 显示字符和 canonical 表示并非总是一对一；逐字符推进映射。
            cell_html: list[str] = []
            canonical_cursor = block.start_char + cell_start
            for character in cell:
                canonical_piece = (
                    r"\|" if character == "|"
                    else "<br>" if character == "\n"
                    else character
                )
                escaped = "<br>" if character == "\n" else html.escape(character)
                piece_end = canonical_cursor + len(canonical_piece)
                if (
                    canonical_cursor < highlight_end
                    and piece_end > highlight_start
                ):
                    cell_html.append(f"<mark>{escaped}</mark>")
                else:
                    cell_html.append(escaped)
                canonical_cursor = piece_end
            output.append(f"<{tag}>{''.join(cell_html)}</{tag}>")
            cell_start += len(rendered_cell) + 3
        output.append("</tr>")
    output.append("</table>")
    return "".join(output)


def build_structured_preview_html(
    canonical_text: str,
    blocks: list[dict[str, Any]] | list[LocatedBlock],
    highlight_start: int | None,
    highlight_end: int | None,
) -> str:
    """按 block 类型安全渲染 structured-v1 来源并精确标记相交字符。"""
    if (highlight_start is None) != (highlight_end is None):
        raise ValueError("高亮起止必须同时提供或同时省略")
    if highlight_start is None:
        render_start, render_end = -1, -1
    else:
        if highlight_start < 0 or highlight_end <= highlight_start:
            raise ValueError("高亮范围非法")
        if highlight_end > len(canonical_text):
            raise ValueError("高亮范围超出 canonical_text")
        render_start, render_end = highlight_start, highlight_end

    validated = [
        block if isinstance(block, LocatedBlock)
        else LocatedBlock.model_validate(block)
        for block in blocks
    ]
    expected = "\n\n".join(block.text for block in validated)
    if expected != canonical_text:
        raise ValueError("blocks 与 canonical_text 不一致")
    for block in validated:
        if canonical_text[block.start_char:block.end_char] != block.text:
            raise ValueError("block offset 与 canonical_text 不一致")

    output: list[str] = []
    for block in validated:
        if block.block_type == "table":
            output.append(_table_html(block, render_start, render_end))
            continue
        content = _escaped_with_highlight(
            block.text,
            block.start_char,
            render_start,
            render_end,
        )
        if block.block_type == "heading":
            level = block.heading_level or 1
            output.append(f"<h{level}>{content}</h{level}>")
        elif block.block_type == "paragraph":
            output.append(f"<p>{content}</p>")
        elif block.block_type == "quote":
            output.append(f"<blockquote>{content}</blockquote>")
        elif block.block_type == "code":
            output.append(f"<pre><code>{content}</code></pre>")
        elif block.block_type == "list_item":
            margin = (block.list_level or 0) * 1.5
            output.append(
                f'<div class="list-item" style="margin-left:{margin}em">'
                f"• {content}</div>"
            )
        else:
            raise ValueError(f"不支持的 block type: {block.block_type}")
    return "".join(output)


def build_structured_document_preview_html(
    units: list[dict[str, Any]],
    target_unit_id: str,
    highlight_start: int,
    highlight_end: int,
) -> str:
    """按原始 unit 顺序渲染整份文档，并只高亮目标 unit。"""
    if not units:
        raise ValueError("整文档预览 units 不能为空")
    output: list[str] = []
    target_found = False
    for unit in units:
        unit_id = unit.get("unit_id")
        location = unit.get("location") or {}
        is_target = unit_id == target_unit_id
        target_found = target_found or is_target
        label = html.escape(location.get("location_label") or "位置未知")
        marker = "🎯 " if is_target else ""
        body = build_structured_preview_html(
            unit.get("canonical_text", ""),
            unit.get("blocks", []),
            highlight_start if is_target else None,
            highlight_end if is_target else None,
        )
        css_class = " preview-target" if is_target else ""
        target_attribute = (
            ' data-citation-preview-target="true"' if is_target else ""
        )
        output.append(
            f'<section class="preview-unit{css_class}"{target_attribute}>'
            f"<h3>{marker}{label}</h3>{body}</section>"
        )
    if not target_found:
        raise ValueError("target_unit_id 不在整文档 units 中")
    return "".join(output)


def fetch_citation_preview(
    backend_url: str,
    citation: dict[str, Any],
) -> dict[str, Any] | None:
    """从后端按需获取引用原文预览。

    返回成功预览数据；失败时返回包含错误信息的结构，便于 UI 区分提示。
    """
    import httpx

    document_id = citation.get("document_id")
    chunk_id = citation.get("chunk_id")
    if not document_id or not chunk_id:
        return {"ok": False, "message": "引用缺少文档或 chunk 信息"}

    url = f"{backend_url}/api/citations/{document_id}/chunks/{chunk_id}/preview"
    try:
        with httpx.Client(trust_env=False, timeout=15) as client:
            resp = client.get(url)
        if resp.status_code == 200:
            return resp.json()
        try:
            detail = resp.json().get("detail", "未知错误")
        except Exception:
            detail = "未知错误"
        return {
            "ok": False,
            "status_code": resp.status_code,
            "message": detail,
        }
    except Exception as error:
        return {"ok": False, "message": f"无法连接后端: {error}"}


def render_citations(
    citations: list[dict[str, Any]],
    message_key: str,
    backend_url: str,
) -> None:
    if not citations:
        return

    with st.expander("📎 参考来源", expanded=False):
        for i, citation in enumerate(citations, 1):
            source_id = citation.get("source_id", i)
            document_name = citation.get("document_name", "未知文档")
            location_label = citation.get("location_label") or "位置未知"
            st.markdown(
                f"**【来源{source_id}】《{document_name}》{location_label}**"
            )
            excerpt = citation.get("excerpt", "")
            if excerpt:
                st.caption(f"> {excerpt[:150]}...")
            if citation.get("preview_available"):
                key = f"preview_btn_{message_key}_{source_id}_{citation.get('chunk_id', i)}"
                if st.button("🔍 查看原文", key=key):
                    _show_preview_dialog(backend_url, citation, message_key, source_id)


@st.dialog("引用原文预览", width="large")
def _show_preview_dialog(
    backend_url: str,
    citation: dict[str, Any],
    message_key: str,
    source_id: int,
) -> None:
    with st.spinner("正在加载原文..."):
        preview = fetch_citation_preview(backend_url, citation)

    if not preview or preview.get("ok") is False:
        message = preview.get("message", "该引用暂无可用原文预览。") if preview else "该引用暂无可用原文预览。"
        st.error(message)
        return

    location = preview.get("location") or {}
    st.markdown(
        f"**文档**：{preview.get('document_name', '')}　"
        f"**位置**：{location.get('location_label', '')}　"
        f"**类型**：{location.get('kind', '')}　"
        f"**Chunk ID**：`{preview.get('chunk_id', '')}`"
    )

    try:
        highlighted_html = build_structured_document_preview_html(
            preview.get("units", []),
            preview.get("target_unit_id", ""),
            preview.get("highlight_start", 0),
            preview.get("highlight_end", 0),
        )
        st.markdown("**📄 完整文档（🎯 所在位置的引用内容已高亮）**")
        st.markdown(highlighted_html, unsafe_allow_html=True)
        render_preview_dialog_behavior()
    except ValueError as err:
        st.error(f"高亮渲染失败: {err}")
