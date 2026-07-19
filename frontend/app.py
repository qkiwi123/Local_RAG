"""
RAG-Knowledge-Base 前端 — Streamlit 聊天界面。

功能布局：
- 侧边栏：文档上传区 + 已上传文档列表 + 清空对话
- 主区域：聊天对话界面（问题 + 答案 + 引用来源）

启动方式：
    streamlit run frontend/app.py
    然后访问 http://localhost:8501

前提：后端已启动（http://localhost:8000）
"""

import streamlit as st
import httpx
import os
from urllib.parse import quote

try:
    # pytest / `python -m` 从项目根目录导入。
    from frontend.http_utils import (
        pending_uploads,
        response_error_detail,
        upload_attempts,
        upload_selection_complete,
    )
    from frontend.trace_view import render_retrieval_trace
except ModuleNotFoundError as error:
    if error.name != "frontend":
        raise
    # `streamlit run frontend/app.py` 会把 frontend/ 放到 sys.path。
    from http_utils import (
        pending_uploads,
        response_error_detail,
        upload_attempts,
        upload_selection_complete,
    )
    from trace_view import render_retrieval_trace

# ─── 页面配置 ────────────────────────────────────────
st.set_page_config(
    page_title="RAG 知识库问答",
    page_icon="📚",
    layout="wide",
)

# ─── 后端地址配置 ────────────────────────────────────
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")

# ─── 初始化会话状态 ─────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []
if "documents" not in st.session_state:
    st.session_state.documents = []
if "_uploaded_set" not in st.session_state:
    # 只记录本次 uploader 选择中已成功入库的文件指纹；失败项必须允许重试。
    st.session_state["_uploaded_set"] = set()
if "_failed_uploads" not in st.session_state:
    # key → 文件名。失败项在用户显式点击重试前不再自动提交。
    st.session_state["_failed_uploads"] = {}
if "_uploader_generation" not in st.session_state:
    # file_uploader 没有 clear API；轮换 key 可在全量成功后重建为空控件。
    st.session_state["_uploader_generation"] = 0


# ─── 工具函数 ────────────────────────────────────────
def refresh_document_list():
    """从后端获取已上传文档列表。"""
    try:
        with httpx.Client(trust_env=False, timeout=5) as client:
            resp = client.get(f"{BACKEND_URL}/api/documents")
        if resp.status_code == 200:
            payload = resp.json()
            items = payload.get("document_items")
            if items is None:
                items = [
                    {"document_id": None, "original_name": name, "status": "legacy"}
                    for name in payload.get("documents", [])
                ]
            st.session_state.documents = items
    except Exception:
        pass


def _delete_document(document: dict):
    """调用后端删除文档（Chroma 向量 + 磁盘文件）。"""
    document_name = document["original_name"]
    document_id = document.get("document_id")
    if document_id:
        url = f"{BACKEND_URL}/api/documents/id/{document_id}"
    else:
        url = f"{BACKEND_URL}/api/documents/{quote(document_name, safe='')}"
    try:
        with httpx.Client(trust_env=False, timeout=10) as client:
            resp = client.delete(url)
        if resp.status_code == 200:
            st.toast(f"「{document_name}」已删除", icon="🗑️")
        else:
            st.toast("删除失败: " + response_error_detail(resp), icon="❌")
    except Exception as e:
        st.toast("删除异常: " + str(e), icon="❌")


def upload_file(uploaded_file) -> bool:
    """上传文件到后端。成功返回 True。"""
    try:
        files = {"file": (uploaded_file.name, uploaded_file.getvalue(),
                          uploaded_file.type or "application/octet-stream")}
        with httpx.Client(trust_env=False, timeout=300) as client:
            resp = client.post(f"{BACKEND_URL}/api/upload", files=files)
        if resp.status_code == 200:
            return True
        else:
            st.error(f"上传失败（HTTP {resp.status_code}）: {response_error_detail(resp)}")
            return False
    except httpx.ConnectError:
        st.error(f"无法连接到后端（{BACKEND_URL}），请先启动后端")
        return False
    except httpx.ReadError:
        st.error(
            "上传过程中后端连接被中断。服务可能正在重启或已异常退出；"
            "请确认后端恢复后点击“重试失败文件”。"
        )
        return False
    except httpx.TimeoutException:
        st.error("上传处理超过 300 秒，请检查后端日志后重试。")
        return False
    except Exception as e:
        st.error(f"上传异常: {str(e)}")
        return False


def ask_question(question: str) -> dict | None:
    """向后端发送问题，返回 {answer, citations} 或 None。"""
    try:
        with httpx.Client(trust_env=False, timeout=120) as client:
            resp = client.post(
                f"{BACKEND_URL}/api/chat",
                json={"question": question},
            )
        if resp.status_code == 200:
            return resp.json()
        else:
            st.error(f"问答失败（HTTP {resp.status_code}）: {response_error_detail(resp)}")
            return None
    except httpx.ConnectError:
        st.error(f"无法连接到后端（{BACKEND_URL}），请先启动后端")
        return None
    except Exception as e:
        st.error(f"问答异常: {str(e)}")
        return None


def backend_is_ready() -> bool:
    """确认本地后端已完成启动，再提交不可幂等的上传请求。"""
    try:
        with httpx.Client(trust_env=False, timeout=3) as client:
            resp = client.get(f"{BACKEND_URL}/")
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


def clear_completed_upload_selection() -> None:
    """清理上传状态并在下次 rerun 使用一个全新的上传控件。"""
    st.session_state["_uploaded_set"].clear()
    st.session_state["_failed_uploads"].clear()
    st.session_state["_uploader_generation"] += 1


# ─── 页面首次加载时初始化文档列表 ──────────────────
# 用临时 key 记住"是否已做过初始加载"，避免每次 rerun 都发请求
if "_initial_load_done" not in st.session_state:
    st.session_state["_initial_load_done"] = True
    refresh_document_list()


# ─── 侧边栏 ──────────────────────────────────────────
with st.sidebar:
    st.title("📚 RAG 知识库")

    # ── 文档上传区 ──
    st.header("📤 上传文档")
    uploaded_files = st.file_uploader(
        "支持 PDF / Word / TXT（可多选）",
        type=["pdf", "docx", "txt"],
        accept_multiple_files=True,
        help="上传后系统将自动解析、切分、向量化并存入知识库",
        key=f"document_uploader_{st.session_state['_uploader_generation']}",
    )

    pending = pending_uploads(
        uploaded_files or [],
        st.session_state["_uploaded_set"],
    )
    pending_keys = {upload_key for upload_key, _ in pending}
    selected_keys = pending_keys | st.session_state["_uploaded_set"]
    failed_uploads = st.session_state["_failed_uploads"]
    for stale_key in set(failed_uploads) - selected_keys:
        failed_uploads.pop(stale_key, None)

    # 兼容修复前已经成功但仍滞留在上传框中的选择。
    if upload_selection_complete(
        uploaded_files or [],
        st.session_state["_uploaded_set"],
    ):
        clear_completed_upload_selection()
        st.rerun()

    retryable = [item for item in pending if item[0] in failed_uploads]
    retry_requested = False
    retry_button_rendered = False
    if retryable:
        st.warning(
            "等待手动重试："
            + "、".join(failed_uploads[key] for key, _ in retryable)
        )
        retry_requested = st.button(
            "重试失败文件",
            type="primary",
            key="retry_failed_uploads",
        )
        retry_button_rendered = True

    attempts = upload_attempts(
        pending,
        set(failed_uploads),
        retry_failed=retry_requested,
    )

    if attempts and not backend_is_ready():
        for upload_key, uploaded_file in attempts:
            failed_uploads[upload_key] = uploaded_file.name
        st.error("后端尚未就绪，未提交文件。请稍后点击“重试失败文件”。")
        if not retry_button_rendered:
            st.button(
                "重试失败文件",
                type="primary",
                key="retry_failed_uploads",
            )
    elif attempts:
        failed_names = []
        success_count = 0
        with st.spinner(f"正在处理 {len(attempts)} 个文件..."):
            for upload_key, uploaded_file in attempts:
                if upload_file(uploaded_file):
                    st.session_state["_uploaded_set"].add(upload_key)
                    failed_uploads.pop(upload_key, None)
                    success_count += 1
                else:
                    failed_uploads[upload_key] = uploaded_file.name
                    failed_names.append(uploaded_file.name)

        if success_count:
            refresh_document_list()

        if upload_selection_complete(
            uploaded_files or [],
            st.session_state["_uploaded_set"],
        ):
            clear_completed_upload_selection()
            st.rerun()

        if failed_names:
            st.warning(
                "失败文件尚未标记为已处理，可直接重试："
                + "、".join(failed_names)
            )
            if not retry_button_rendered:
                st.button(
                    "重试失败文件",
                    type="primary",
                    key="retry_failed_uploads",
                )
        elif success_count:
            st.rerun()

    st.divider()

    # ── 已上传文档列表 ──
    st.header("📋 已上传文档")
    if st.button("🔄 刷新列表"):
        refresh_document_list()
        st.rerun()

    if st.session_state.documents:
        for doc in st.session_state.documents:
            doc_name = doc["original_name"]
            doc_id = doc.get("document_id")
            col1, col2 = st.columns([4, 1])
            with col1:
                st.text(f"📄 {doc_name}")
                if doc_id:
                    chunks = doc.get("chunk_count")
                    detail = f"ID: {doc_id[:8]}"
                    if chunks is not None:
                        detail += f" · {chunks} chunks"
                    st.caption(detail)
            with col2:
                key = doc_id or doc_name
                if st.button("🗑️", key=f"del_{key}", help=f"删除 {doc_name}"):
                    _delete_document(doc)
                    refresh_document_list()
                    st.rerun()
    else:
        st.caption("（暂无文档，请先上传）")

    st.divider()

    # ── 清空对话 ──
    if st.button("🗑️ 清空对话"):
        st.session_state.messages = []
        st.rerun()

    st.divider()
    st.caption(f"后端: {BACKEND_URL}")


# ─── 主区域：对话界面 ────────────────────────────────
st.title("💬 知识库问答")

if not st.session_state.messages:
    st.info(
        "👋 欢迎使用企业知识库问答系统！\n\n"
        "1. 先在左侧上传文档（支持 PDF / Word / TXT，可多选）\n"
        "2. 然后在下方输入问题，系统将从文档中检索并回答\n\n"
        "⚠️ 请确认服务已启动（`.\\scripts\\start.ps1`）"
    )

# 渲染历史对话
for message_index, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("citations"):
            with st.expander("📎 参考来源", expanded=False):
                for i, citation in enumerate(msg["citations"], 1):
                    source_id = citation.get("source_id", i)
                    st.markdown(
                        f"**【来源{source_id}】《{citation['document_name']}》"
                        f"第{citation['page_number']}页**"
                    )
                    if citation.get("excerpt"):
                        st.caption(f"> {citation['excerpt'][:150]}...")
        if msg["role"] == "assistant":
            render_retrieval_trace(msg.get("trace"), str(message_index))


# 输入框
if question := st.chat_input("请输入您的问题..."):
    if not st.session_state.documents:
        st.warning("⚠️ 请先在左侧上传至少一份文档")
        st.stop()

    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("正在思考..."):
            result = ask_question(question)

        if result is None:
            st.stop()

        answer = result.get("answer", "")
        citations = result.get("citations", [])
        trace = result.get("trace")
        st.markdown(answer)

        if citations:
            with st.expander("📎 参考来源", expanded=False):
                for i, citation in enumerate(citations, 1):
                    source_id = citation.get("source_id", i)
                    st.markdown(
                        f"**【来源{source_id}】《{citation['document_name']}》"
                        f"第{citation['page_number']}页**"
                    )
                    if citation.get("excerpt"):
                        st.caption(f"> {citation['excerpt'][:150]}...")

        render_retrieval_trace(
            trace,
            str(len(st.session_state.messages)),
        )

    st.session_state.messages.append({
        "role": "assistant",
        "content": answer,
        "citations": citations,
        "trace": trace,
    })
