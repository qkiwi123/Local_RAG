"""前端 HTTP 错误与上传状态辅助函数。

该模块不依赖 Streamlit，便于对空响应、非 JSON 响应以及上传重试状态做单元测试。
"""

from hashlib import sha256
from typing import Any, Iterable


def response_error_detail(response: Any) -> str:
    """从后端响应中提取可展示的错误，空响应也返回稳定文案。"""
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        detail = payload.get("detail")
        if detail not in (None, ""):
            return str(detail)

    body = response.text.strip()
    if body:
        return body
    return f"HTTP {response.status_code}（后端未返回错误详情）"


def upload_identity(uploaded_file: Any) -> str:
    """用文件名、大小和内容摘要标识一次文件选择。"""
    content = uploaded_file.getvalue()
    digest = sha256(content).hexdigest()
    return f"{uploaded_file.name}:{len(content)}:{digest}"


def pending_uploads(
    uploaded_files: Iterable[Any],
    completed_uploads: set[str],
) -> list[tuple[str, Any]]:
    """同步当前选择并返回未成功处理的文件。

    失败项不会写入 ``completed_uploads``，因此下一次 rerun 可以重试；文件从
    uploader 移除后，其完成标记也会清除，重新选择同一文件可创建新版本。
    """
    keyed_files = [(upload_identity(file), file) for file in uploaded_files]
    selected_keys = {key for key, _ in keyed_files}
    completed_uploads.intersection_update(selected_keys)
    return [item for item in keyed_files if item[0] not in completed_uploads]


def upload_attempts(
    pending: Iterable[tuple[str, Any]],
    failed_keys: set[str],
    retry_failed: bool,
) -> list[tuple[str, Any]]:
    """选择本轮应提交的文件；历史失败项必须经过显式重试。"""
    pending_items = list(pending)
    attempts = [item for item in pending_items if item[0] not in failed_keys]
    if retry_failed:
        attempts.extend(item for item in pending_items if item[0] in failed_keys)
    return attempts


def upload_selection_complete(
    uploaded_files: Iterable[Any],
    completed_uploads: set[str],
) -> bool:
    """当前选择非空且全部成功入库时返回 True。"""
    selected_files = list(uploaded_files)
    return bool(selected_files) and all(
        upload_identity(file) in completed_uploads for file in selected_files
    )
