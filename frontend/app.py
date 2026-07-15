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

# ─── 页面配置 ────────────────────────────────────────
st.set_page_config(
    page_title="RAG 知识库问答",
    page_icon="📚",
    layout="wide",
)

# ─── 后端地址配置 ────────────────────────────────────
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

# ─── 初始化会话状态 ─────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []
if "documents" not in st.session_state:
    st.session_state.documents = []
if "_uploaded_set" not in st.session_state:
    st.session_state["_uploaded_set"] = set()  # 已处理的文件名，防死循环


# ─── 工具函数 ────────────────────────────────────────
def refresh_document_list():
    """从后端获取已上传文档列表。"""
    try:
        resp = httpx.get(f"{BACKEND_URL}/api/documents", timeout=5)
        if resp.status_code == 200:
            st.session_state.documents = resp.json().get("documents", [])
    except Exception:
        pass


def _delete_document(document_name: str):
    """调用后端删除文档（Chroma 向量 + 磁盘文件）。"""
    try:
        resp = httpx.delete(
            f"{BACKEND_URL}/api/documents/{document_name}",
            timeout=10,
        )
        if resp.status_code == 200:
            st.toast(f"「{document_name}」已删除", icon="🗑️")
        else:
            st.toast("删除失败: " + resp.json().get("detail", "未知"), icon="❌")
    except Exception as e:
        st.toast("删除异常: " + str(e), icon="❌")


def upload_file(uploaded_file) -> bool:
    """上传文件到后端。成功返回 True。"""
    try:
        files = {"file": (uploaded_file.name, uploaded_file.getvalue(),
                          uploaded_file.type or "application/octet-stream")}
        resp = httpx.post(f"{BACKEND_URL}/api/upload", files=files, timeout=300)
        if resp.status_code == 200:
            return True
        else:
            st.error(f"上传失败: {resp.json().get('detail', resp.text)}")
            return False
    except httpx.ConnectError:
        st.error(f"无法连接到后端（{BACKEND_URL}），请先启动后端")
        return False
    except httpx.ReadError:
        st.error("后端连接被关闭。文件可能过大或处理超时，请重试。")
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
            st.error(f"问答失败: {resp.json().get('detail', resp.text)}")
            return None
    except httpx.ConnectError:
        st.error(f"无法连接到后端（{BACKEND_URL}），请先启动后端")
        return None
    except Exception as e:
        st.error(f"问答异常: {str(e)}")
        return None


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
    )

    if uploaded_files:
        # 只处理新文件（未在 _uploaded_set 中记录过的），防止 rerun 死循环
        new_files = [f for f in uploaded_files
                     if f.name not in st.session_state["_uploaded_set"]]
        if new_files:
            with st.spinner(f"正在处理 {len(new_files)} 个文件..."):
                for f in new_files:
                    upload_file(f)
                    st.session_state["_uploaded_set"].add(f.name)
            refresh_document_list()
            st.rerun()
        # 全已处理 → 不做事

    st.divider()

    # ── 已上传文档列表 ──
    st.header("📋 已上传文档")
    if st.button("🔄 刷新列表"):
        refresh_document_list()
        st.rerun()

    if st.session_state.documents:
        for doc in st.session_state.documents:
            col1, col2 = st.columns([4, 1])
            with col1:
                st.text(f"📄 {doc}")
            with col2:
                if st.button("🗑️", key=f"del_{doc}", help=f"删除 {doc}"):
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
        "⚠️ 请确认后端已启动（`uvicorn backend.main:app --reload`）"
    )

# 渲染历史对话
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("citations"):
            with st.expander("📎 参考来源", expanded=False):
                for i, citation in enumerate(msg["citations"], 1):
                    st.markdown(
                        f"**{i}. 《{citation['document_name']}》"
                        f"第{citation['page_number']}页**"
                    )
                    if citation.get("excerpt"):
                        st.caption(f"> {citation['excerpt'][:150]}...")


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
        st.markdown(answer)

        if citations:
            with st.expander("📎 参考来源", expanded=False):
                for i, citation in enumerate(citations, 1):
                    st.markdown(
                        f"**{i}. 《{citation['document_name']}》"
                        f"第{citation['page_number']}页**"
                    )
                    if citation.get("excerpt"):
                        st.caption(f"> {citation['excerpt'][:150]}...")

    st.session_state.messages.append({
        "role": "assistant",
        "content": answer,
        "citations": citations,
    })
