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

import os
import sys
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import httpx
import streamlit as st


# Streamlit 会把脚本目录 frontend/ 放到 sys.path 首位，但不会保证仓库根目录
# 可导入。显式加入项目根目录，使 `streamlit run frontend/app.py` 与 pytest、
# `python -m` 使用同一套 frontend/backend 绝对导入。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from frontend.http_utils import response_error_detail, upload_identity
from frontend.trace_view import render_retrieval_trace
from frontend.citation_view import render_citations

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
if "conversations" not in st.session_state:
    st.session_state.conversations = []
if "current_conversation_id" not in st.session_state:
    st.session_state.current_conversation_id = None
if "tag_tree" not in st.session_state:
    # [{tag_path, name, document_count}, ...]；过渡期仅用于选择器与筛选器。
    st.session_state.tag_tree = []
if "chat_tag_filter" not in st.session_state:
    # 当前生效的标签筛选（路径列表）；空列表 = 全库检索。
    st.session_state.chat_tag_filter = []
if "_staged_files" not in st.session_state:
    # 上传暂存区：upload_identity -> UploadedFile。选择文件只暂存，不自动入库。
    st.session_state["_staged_files"] = {}
if "_staged_failed" not in st.session_state:
    # 暂存区内上传失败的 upload_identity 集合；确认上传时自动重试。
    st.session_state["_staged_failed"] = set()
if "upload_tag_selection" not in st.session_state:
    # 本次上传将携带的标签路径列表（与上传区 multiselect 共用此 key）。
    st.session_state["upload_tag_selection"] = []
if "_edit_tags_for" not in st.session_state:
    # 正在编辑标签的文档（document_id 或展示名）；None 表示未展开编辑区。
    st.session_state["_edit_tags_for"] = None
if "_uploader_generation" not in st.session_state:
    # file_uploader 没有 clear API；轮换 key 可在全部暂存文件处理完后重建为空控件。
    st.session_state["_uploader_generation"] = 0


# ─── 工具函数 ────────────────────────────────────────
def refresh_tag_tree():
    """从后端获取标签树（含文档数），供上传选择器与检索筛选器使用。"""
    try:
        with httpx.Client(trust_env=False, timeout=5) as client:
            resp = client.get(f"{BACKEND_URL}/api/tags")
        if resp.status_code == 200:
            payload = resp.json()
            if isinstance(payload, list):
                st.session_state.tag_tree = payload
    except Exception:
        pass


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


def upload_file(uploaded_file, tags=None) -> bool:
    """上传文件到后端，可附带标签列表。成功返回 True。"""
    try:
        files = {"file": (uploaded_file.name, uploaded_file.getvalue(),
                          uploaded_file.type or "application/octet-stream")}
        data = {"tags": list(tags or [])} if tags else None
        with httpx.Client(trust_env=False, timeout=300) as client:
            resp = client.post(
                f"{BACKEND_URL}/api/upload", files=files, data=data
            )
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


def refresh_conversations() -> None:
    try:
        with httpx.Client(trust_env=False, timeout=5) as client:
            response = client.get(f"{BACKEND_URL}/api/conversations")
        if response.status_code == 200:
            st.session_state.conversations = response.json()
    except Exception:
        pass


def load_conversation(conversation_id: str) -> bool:
    try:
        with httpx.Client(trust_env=False, timeout=5) as client:
            response = client.get(
                f"{BACKEND_URL}/api/conversations/{conversation_id}"
            )
        if response.status_code != 200:
            return False
        payload = response.json()
        st.session_state.current_conversation_id = conversation_id
        st.session_state.messages = payload.get("messages", [])
        return True
    except Exception:
        return False


def create_new_conversation() -> bool:
    try:
        with httpx.Client(trust_env=False, timeout=5) as client:
            response = client.post(
                f"{BACKEND_URL}/api/conversations",
                json={},
            )
        if response.status_code != 200:
            return False
        conversation = response.json()
        refresh_conversations()
        return load_conversation(conversation["conversation_id"])
    except Exception:
        return False


def ask_question(
    question: str,
    *,
    request_id: str | None = None,
    retry_failed: bool = False,
    tags: list[str] | None = None,
) -> dict | None:
    """向后端发送持久化问题；tags 为可选的标签筛选（空列表 = 全库）。"""
    conversation_id = st.session_state.current_conversation_id
    complete_messages = [
        message
        for message in st.session_state.messages
        if message.get("status", "complete") == "complete"
    ]
    expected_last = (
        complete_messages[-1].get("message_id")
        if complete_messages else None
    )
    try:
        with httpx.Client(trust_env=False, timeout=120) as client:
            resp = client.post(
                f"{BACKEND_URL}/api/chat",
                json={
                    "question": question,
                    "conversation_id": conversation_id,
                    "request_id": request_id or uuid4().hex,
                    "expected_last_message_id": expected_last,
                    "retry_failed": retry_failed,
                    "tags": list(tags or []),
                },
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
    """上传区全部处理后轮换控件 key，重建为空上传框。"""
    st.session_state["_uploader_generation"] += 1


def create_tag_and_select(raw_path: str) -> None:
    """新建空标签；成功后自动选入本次上传标签并刷新标签树。"""
    cleaned = (raw_path or "").strip()
    if not cleaned:
        st.warning("请输入标签路径")
        return
    try:
        with httpx.Client(trust_env=False, timeout=10) as client:
            resp = client.post(
                f"{BACKEND_URL}/api/tags", json={"tag_path": cleaned}
            )
        if resp.status_code == 200:
            created = resp.json()["tag_path"]
            selection = list(st.session_state["upload_tag_selection"])
            if created not in selection:
                selection.append(created)
                st.session_state["upload_tag_selection"] = selection
            refresh_tag_tree()
            st.toast(f"标签「{created}」已创建并选中", icon="🏷")
            st.rerun()
        else:
            st.error("新建标签失败: " + response_error_detail(resp))
    except Exception as error:
        st.error("新建标签异常: " + str(error))


def process_staged_uploads() -> None:
    """把暂存区文件逐个提交（带当前标签选择）；成功移除，失败保留可重试。"""
    staged = st.session_state["_staged_files"]
    failed = st.session_state["_staged_failed"]
    tags = list(st.session_state["upload_tag_selection"])
    success_keys = []
    with st.spinner(f"正在上传 {len(staged)} 个文件..."):
        for key, uploaded_file in list(staged.items()):
            if upload_file(uploaded_file, tags=tags):
                success_keys.append(key)
                failed.discard(key)
            else:
                failed.add(key)
    for key in success_keys:
        staged.pop(key, None)
    if success_keys:
        refresh_document_list()
        refresh_tag_tree()
    if not staged:
        clear_completed_upload_selection()


def save_document_tags(document: dict, paths: list[str]) -> bool:
    """整体替换已上传文档的标签集；成功返回 True。"""
    document_id = document.get("document_id")
    if not document_id:
        st.error("该文档缺少稳定 ID，无法修改标签")
        return False
    try:
        with httpx.Client(trust_env=False, timeout=10) as client:
            resp = client.put(
                f"{BACKEND_URL}/api/documents/id/{document_id}/tags",
                json={"tags": paths},
            )
        if resp.status_code == 200:
            st.session_state["_edit_tags_for"] = None
            refresh_document_list()
            refresh_tag_tree()
            st.toast(f"「{document['original_name']}」标签已更新", icon="🏷")
            return True
        st.error("保存标签失败: " + response_error_detail(resp))
        return False
    except Exception as error:
        st.error("保存标签异常: " + str(error))
        return False


# ─── 页面首次加载时初始化文档列表 ──────────────────
# 用临时 key 记住"是否已做过初始加载"，避免每次 rerun 都发请求
if "_initial_load_done" not in st.session_state:
    st.session_state["_initial_load_done"] = True
    refresh_document_list()
    refresh_conversations()
    refresh_tag_tree()
    available_ids = {
        item["conversation_id"] for item in st.session_state.conversations
    }
    if st.session_state.current_conversation_id in available_ids:
        load_conversation(st.session_state.current_conversation_id)
    elif st.session_state.conversations:
        load_conversation(st.session_state.conversations[0]["conversation_id"])


# ─── 侧边栏 ──────────────────────────────────────────
with st.sidebar:
    st.title("📚 RAG 知识库")

    st.header("💬 历史对话")
    if st.button("➕ 新建对话", type="primary", use_container_width=True):
        if create_new_conversation():
            st.rerun()
        st.error("新建对话失败，请确认后端可用")

    for conversation in st.session_state.conversations:
        conversation_id = conversation["conversation_id"]
        label = conversation["title"]
        if conversation_id == st.session_state.current_conversation_id:
            label = "▶ " + label
        if st.button(
            label,
            key=f"conversation_{conversation_id}",
            use_container_width=True,
        ):
            if load_conversation(conversation_id):
                st.rerun()

    current_id = st.session_state.current_conversation_id
    if current_id:
        current = next(
            (
                item for item in st.session_state.conversations
                if item["conversation_id"] == current_id
            ),
            None,
        )
        with st.expander("管理当前对话"):
            title = st.text_input(
                "标题",
                value=current["title"] if current else "",
                key=f"conversation_title_{current_id}",
            )
            if st.button("保存标题", key=f"rename_{current_id}"):
                with httpx.Client(trust_env=False, timeout=5) as client:
                    response = client.patch(
                        f"{BACKEND_URL}/api/conversations/{current_id}",
                        json={"title": title},
                    )
                if response.status_code == 200:
                    refresh_conversations()
                    st.rerun()
                st.error("重命名失败")
            confirm_key = f"_confirm_delete_{current_id}"
            if st.button("删除当前对话", key=f"delete_{current_id}"):
                st.session_state[confirm_key] = True
            if st.session_state.get(confirm_key):
                st.warning("删除后无法恢复，确定继续？")
                yes, no = st.columns(2)
                if yes.button("确定删除", key=f"confirm_{current_id}"):
                    with httpx.Client(trust_env=False, timeout=5) as client:
                        response = client.delete(
                            f"{BACKEND_URL}/api/conversations/{current_id}"
                        )
                    if response.status_code == 200:
                        st.session_state.current_conversation_id = None
                        st.session_state.messages = []
                        refresh_conversations()
                        st.rerun()
                if no.button("取消", key=f"cancel_{current_id}"):
                    st.session_state.pop(confirm_key, None)
                    st.rerun()

    st.divider()

    # ── 检索范围筛选（过渡期最小实现） ──
    st.header("🔍 检索范围")
    tag_paths = sorted(tag["tag_path"] for tag in st.session_state.tag_tree)
    selected_filter = st.multiselect(
        "按标签筛选（可多选，含子标签；不选 = 全库）",
        options=tag_paths,
        key="chat_tag_filter",
        help="选中父级标签会自动包含其全部子标签；多个标签取并集",
    )
    if selected_filter:
        st.caption("当前筛选：" + "、".join(selected_filter))

    st.divider()

    # ── 标签管理（独立新建空标签） ──
    st.header("🏷 标签管理")
    with st.expander("新建与查看标签", expanded=False):
        st.text_input(
            "新标签路径（如：课程课件/数据结构）",
            key="tag_mgmt_new_input",
        )
        if st.button("➕ 新建标签", key="tag_mgmt_create"):
            create_tag_and_select(st.session_state["tag_mgmt_new_input"])
        if st.session_state.tag_tree:
            for tag in sorted(
                st.session_state.tag_tree, key=lambda item: item["tag_path"]
            ):
                direct = tag.get("direct_document_count", 0)
                subtree = tag.get("document_count", 0)
                if subtree == direct:
                    st.caption(f"{tag['tag_path']}（{subtree} 文档）")
                else:
                    st.caption(
                        f"{tag['tag_path']}（本层 {direct} · 含子级 {subtree}）"
                    )
        else:
            st.caption("（暂无标签，可在此新建空标签）")

    st.divider()

    # ── 文档上传区（三段式：暂存 → 选标签 → 确认上传） ──
    st.header("📤 上传文档")
    uploaded_files = st.file_uploader(
        "支持 PDF / Word / TXT / Markdown / PPTX（可多选）",
        type=["pdf", "docx", "txt", "md", "pptx"],
        accept_multiple_files=True,
        help="选择文件只会暂存，需点击「确认上传」才会解析入库",
        key=f"document_uploader_{st.session_state['_uploader_generation']}",
    )

    # 同步暂存区与当前选择：新选择的进入暂存区，取消选择的移出暂存区。
    current_files = {
        upload_identity(file): file for file in (uploaded_files or [])
    }
    staged = st.session_state["_staged_files"]
    failed = st.session_state["_staged_failed"]
    for stale_key in set(staged) - set(current_files):
        staged.pop(stale_key, None)
        failed.discard(stale_key)
    for upload_key, file in current_files.items():
        staged.setdefault(upload_key, file)

    # 标签选择 + 上传时新建标签（创建后自动选入本次上传）
    tag_paths = sorted(tag["tag_path"] for tag in st.session_state.tag_tree)
    st.multiselect(
        "本次上传的标签（可选，可多选）",
        options=tag_paths,
        key="upload_tag_selection",
    )
    upload_new_tag = st.text_input(
        "上传时新建标签路径（创建后自动选中）",
        key="upload_new_tag_input",
    )
    if st.button("➕ 新建并选中", key="upload_create_tag"):
        create_tag_and_select(upload_new_tag)

    # 暂存区与确认上传
    if staged:
        staged_names = [file.name for file in staged.values()]
        st.info("暂存 " + "、".join(staged_names))
        failed_count = len(set(staged) & failed)
        if failed_count:
            st.warning("有文件上传失败，点击下方按钮重试（失败的仅保留在暂存区）")
        confirm_label = (
            f"🔁 重试失败文件（{failed_count}）"
            if failed_count
            else f"✅ 确认上传（{len(staged)} 个文件）"
        )
        if st.button(confirm_label, type="primary", key="confirm_staged_upload"):
            if backend_is_ready():
                process_staged_uploads()
                st.rerun()
            else:
                st.error("后端尚未就绪，暂存文件未提交，请稍后重试")
    else:
        st.caption("选择文件后暂存于此，点击确认上传才会解析入库")

    st.divider()

    # ── 已上传文档列表 ──
    st.header("📋 已上传文档")
    if st.button("🔄 刷新列表"):
        refresh_document_list()
        refresh_tag_tree()
        st.rerun()

    tag_paths = sorted(tag["tag_path"] for tag in st.session_state.tag_tree)
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
                    doc_tags = doc.get("tags") or []
                    if doc_tags:
                        detail += " · 🏷 " + "、".join(doc_tags)
                    st.caption(detail)
            with col2:
                key = doc_id or doc_name
                if st.button("🏷", key=f"tagbtn_{key}", help=f"编辑 {doc_name} 的标签"):
                    st.session_state["_edit_tags_for"] = key
                    st.rerun()
                if st.button("🗑️", key=f"del_{key}", help=f"删除 {doc_name}"):
                    _delete_document(doc)
                    refresh_document_list()
                    refresh_tag_tree()
                    st.rerun()

            if doc_id and st.session_state["_edit_tags_for"] == key:
                with st.expander("编辑标签", expanded=True):
                    select_key = f"edit_tags_select_{key}"
                    if select_key not in st.session_state:
                        st.session_state[select_key] = doc.get("tags") or []
                    selected = st.multiselect(
                        "标签",
                        options=tag_paths,
                        key=select_key,
                    )
                    new_text = st.text_input(
                        "新标签（多个用逗号分隔，保存后会挂到当前文档）",
                        key=f"edit_tags_new_{key}",
                    )
                    merged = sorted(
                        set(selected)
                        | {
                            part.strip()
                            for part in new_text.replace("，", ",").split(",")
                            if part.strip()
                        }
                    )
                    save_col, cancel_col = st.columns(2)
                    if save_col.button("保存标签", key=f"edit_tags_save_{key}"):
                        if save_document_tags(doc, merged):
                            st.rerun()
                    if cancel_col.button("取消", key=f"edit_tags_cancel_{key}"):
                        st.session_state["_edit_tags_for"] = None
                        st.rerun()
    else:
        st.caption("（暂无文档，请先上传）")

    st.divider()

    st.caption(f"后端: {BACKEND_URL}")


# ─── 主区域：对话界面 ────────────────────────────────
st.title("💬 知识库问答")

if not st.session_state.messages:
    st.info(
        "👋 欢迎使用企业知识库问答系统！\n\n"
        "1. 先在左侧上传文档（支持 PDF / Word / TXT / Markdown / PPTX，可多选）\n"
        "2. 然后直接在下方输入问题，系统会自动创建对话记录并从文档中检索回答\n\n"
        "⚠️ 请确认服务已启动（`.\\scripts\\start.ps1`）"
    )

# 渲染历史对话
for message_index, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("status") == "failed":
            st.error("该消息处理失败，可在会话末尾重新发送。")
            if (
                msg.get("role") == "user"
                and message_index == len(st.session_state.messages) - 1
                and st.button(
                    "重试该消息",
                    key=f"retry_message_{msg.get('message_id', message_index)}",
                )
            ):
                result = ask_question(
                    msg["content"],
                    request_id=msg.get("request_id"),
                    retry_failed=True,
                )
                if result is not None:
                    load_conversation(st.session_state.current_conversation_id)
                    refresh_conversations()
                    st.rerun()
        if msg.get("citations"):
            render_citations(
                msg["citations"],
                f"history_{message_index}",
                BACKEND_URL,
            )
        if msg["role"] == "assistant":
            render_retrieval_trace(msg.get("trace"), str(message_index))


# 输入框
if question := st.chat_input(
    "请输入您的问题...",
):
    if not st.session_state.documents:
        st.warning("⚠️ 请先在左侧上传至少一份文档")
        st.stop()

    if (
        not st.session_state.current_conversation_id
        and not create_new_conversation()
    ):
        st.error("无法自动创建对话，请确认后端可用后重试")
        st.stop()

    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("正在思考..."):
            result = ask_question(
                question,
                tags=st.session_state.chat_tag_filter,
            )

        if result is None:
            st.stop()

        answer = result.get("answer", "")
        citations = result.get("citations", [])
        trace = result.get("trace")

    assistant_message_id = result.get("assistant_message_id")
    load_conversation(st.session_state.current_conversation_id)
    for message in reversed(st.session_state.messages):
        if message.get("role") != "assistant":
            continue
        if (
            assistant_message_id is None
            or message.get("message_id") == assistant_message_id
        ):
            message["trace"] = trace
            break
    refresh_conversations()
    st.rerun()
