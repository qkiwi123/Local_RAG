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

# ─── 页面配置 ────────────────────────────────────────
st.set_page_config(
    page_title="RAG 知识库问答",
    page_icon="📚",
    layout="wide",
)

# ─── 后端地址配置 ────────────────────────────────────
import os
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

# ─── 初始化会话状态 ─────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []  # 对话历史
if "documents" not in st.session_state:
    st.session_state.documents = []  # 已上传文档列表


# ─── 工具函数 ────────────────────────────────────────
def refresh_document_list():
    """从后端获取已上传文档列表。"""
    try:
        resp = httpx.get(f"{BACKEND_URL}/api/documents", timeout=5)
        if resp.status_code == 200:
            st.session_state.documents = resp.json().get("documents", [])
    except Exception:
        # 后端未启动或网络错误时静默失败
        pass


def upload_file(uploaded_file) -> bool:
    """上传文件到后端。成功返回 True。"""
    try:
        files = {"file": (uploaded_file.name, uploaded_file.getvalue(),
                          uploaded_file.type or "application/octet-stream")}
        resp = httpx.post(f"{BACKEND_URL}/api/upload", files=files, timeout=120)
        if resp.status_code == 200:
            return True
        else:
            error_detail = resp.json().get("detail", resp.text)
            st.error(f"上传失败: {error_detail}")
            return False
    except httpx.ConnectError:
        st.error(f"无法连接到后端，请确认后端已启动（{BACKEND_URL}）")
        return False
    except Exception as e:
        st.error(f"上传异常: {str(e)}")
        return False


def ask_question(question: str) -> dict | None:
    """向后端发送问题，返回 {answer, citations} 或 None。"""
    try:
        resp = httpx.post(
            f"{BACKEND_URL}/api/chat",
            json={"question": question},
            timeout=120,
        )
        if resp.status_code == 200:
            return resp.json()
        else:
            error_detail = resp.json().get("detail", resp.text)
            st.error(f"问答失败: {error_detail}")
            return None
    except httpx.ConnectError:
        st.error(f"无法连接到后端，请确认后端已启动（{BACKEND_URL}）")
        return None
    except Exception as e:
        st.error(f"问答异常: {str(e)}")
        return None


# ─── 侧边栏 ──────────────────────────────────────────
with st.sidebar:
    st.title("📚 RAG 知识库")

    # ── 文档上传区 ──
    st.header("📤 上传文档")
    uploaded_file = st.file_uploader(
        "支持 PDF / Word / TXT",
        type=["pdf", "docx", "txt"],
        help="上传后系统将自动解析、切分、向量化并存入知识库",
    )

    if uploaded_file is not None:
        with st.spinner(f"正在处理 {uploaded_file.name}..."):
            success = upload_file(uploaded_file)
            if success:
                st.success(f"✅ {uploaded_file.name} 已入库")
                refresh_document_list()
                st.rerun()

    st.divider()

    # ── 已上传文档列表 ──
    st.header("📋 已上传文档")
    if st.button("🔄 刷新列表"):
        refresh_document_list()
        st.rerun()

    if st.session_state.documents:
        for doc in st.session_state.documents:
            st.text(f"📄 {doc}")
    else:
        st.caption("（暂无文档，请先上传）")

    st.divider()

    # ── 清空对话 ──
    if st.button("🗑️ 清空对话"):
        st.session_state.messages = []
        st.rerun()

    # ── 状态信息 ──
    st.divider()
    st.caption(f"后端地址: {BACKEND_URL}")


# ─── 主区域：对话界面 ────────────────────────────────
st.title("💬 知识库问答")

# 欢迎提示
if not st.session_state.messages:
    st.info(
        "👋 欢迎使用企业知识库问答系统！\n\n"
        "1. 先在左侧上传文档（PDF / Word / TXT）\n"
        "2. 然后在下方输入问题，系统将从文档中检索并回答\n\n"
        "⚠️ 首次使用时，请确认后端已启动（`uvicorn backend.main:app --reload`）"
    )

# 渲染历史对话
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

        # 如果有引用来源，用折叠面板展示
        if msg.get("citations"):
            with st.expander("📎 参考来源", expanded=False):
                for i, citation in enumerate(msg["citations"], 1):
                    st.markdown(f"**{i}. 《{citation['document_name']}》第{citation['page_number']}页**")
                    if citation.get("excerpt"):
                        st.caption(f"> {citation['excerpt'][:150]}...")


# 输入框
if question := st.chat_input("请输入您的问题..."):
    # 检查是否有文档
    if not st.session_state.documents:
        st.warning("⚠️ 请先在左侧上传至少一份文档")
        st.stop()

    # 1. 显示用户问题
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    # 2. 调用后端获取答案
    with st.chat_message("assistant"):
        with st.spinner("正在思考..."):
            result = ask_question(question)

        if result is None:
            # ask_question 内部已显示错误
            st.stop()

        answer = result.get("answer", "")
        citations = result.get("citations", [])

        # 显示答案
        st.markdown(answer)

        # 显示引用来源
        if citations:
            with st.expander("📎 参考来源", expanded=False):
                for i, citation in enumerate(citations, 1):
                    st.markdown(
                        f"**{i}. 《{citation['document_name']}》"
                        f"第{citation['page_number']}页**"
                    )
                    if citation.get("excerpt"):
                        st.caption(f"> {citation['excerpt'][:150]}...")

    # 3. 保存到会话历史
    st.session_state.messages.append({
        "role": "assistant",
        "content": answer,
        "citations": citations,
    })
