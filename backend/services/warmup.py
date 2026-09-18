"""模型预热：把 Embedding/Reranker 的首次加载成本挪到服务启动期。

后台线程执行，不阻塞健康检查；失败仅告警，不影响服务可用性。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from backend.config import settings

logger = logging.getLogger(__name__)

_status_lock = threading.Lock()
_status: dict[str, Any] = {
    "status": "not_started",
    "component": None,
    "error_type": None,
    "detail": None,
}


def _set_status(
    status: str,
    *,
    component: str | None = None,
    error: Exception | None = None,
) -> None:
    with _status_lock:
        _status.update(
            {
                "status": status,
                "component": component,
                "error_type": type(error).__name__ if error else None,
                "detail": str(error) if error else None,
            }
        )


def get_warmup_status() -> dict[str, Any]:
    """返回模型预热状态的只读快照，供健康检查判断是否真正就绪。"""
    with _status_lock:
        return dict(_status)


def warmup_models() -> bool:
    """加载本地模型并记录状态；任一组件失败时返回 False。"""
    _set_status("warming", component="embedding")
    try:
        from backend.services.embedding import embed_texts

        embed_texts(["预热"])
    except Exception as error:
        logger.exception("Embedding 预热失败")
        _set_status("failed", component="embedding", error=error)
        # Embedding 失败后不再二次导入依赖 PyTorch 的 Reranker。Windows DLL
        # 初始化失败时，重复导入一个半初始化的 torch 进程可能直接原生崩溃。
        return False

    if not settings.RERANKER_ENABLED:
        _set_status("ready")
        return True

    _set_status("warming", component="reranker")
    try:
        from backend.services.reranker import rerank

        rerank(
            "预热",
            [{"chunk_id": "warmup", "content": "预热", "metadata": {}}],
        )
    except Exception as error:
        logger.exception("Reranker 预热失败")
        _set_status("failed", component="reranker", error=error)
        return False

    _set_status("ready")
    return True


def start_background_warmup() -> threading.Thread | None:
    """服务启动时异步预热；关闭开关时不执行。"""
    if not settings.PREWARM_MODELS:
        _set_status("disabled")
        return None

    _set_status("pending")
    thread = threading.Thread(target=warmup_models, daemon=True)
    thread.start()
    return thread
