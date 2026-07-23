"""Streamlit 引用原文预览组件。

提供：
- build_highlight_html: 安全 HTML 转义后精确包裹 <mark>
- fetch_citation_preview: lazy-load 预览数据
- render_citations: 统一引用列表 + 按需预览对话框
"""

import html
from typing import Any

import streamlit as st


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


# 以下渲染逻辑保持不变

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
            page_number = citation.get("page_number", "?")
            st.markdown(f"**【来源{source_id}】《{document_name}》第{page_number}页**")
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

    st.markdown(
        f"**文档**：{preview.get('document_name', '')}　"
        f"**页码**：{preview.get('page_number', '')}　"
        f"**类型**：{preview.get('content_type', '')}　"
        f"**Chunk ID**：`{preview.get('chunk_id', '')}`"
    )

    try:
        highlighted_html = build_highlight_html(
            preview.get("parsed_page_text", ""),
            preview.get("highlight_start", 0),
            preview.get("highlight_end", 0),
        )
        st.markdown("**📄 完整解析页（被引用部分已高亮）**")
        st.markdown(highlighted_html, unsafe_allow_html=True)
    except ValueError as err:
        st.error(f"高亮渲染失败: {err}")

    chunk_content = preview.get("chunk_content", "")
    if chunk_content:
        with st.expander("📋 完整 Chunk 原文", expanded=False):
            st.code(chunk_content, language=None)
