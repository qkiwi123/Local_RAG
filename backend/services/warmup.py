"""模型预热：把 Embedding/Reranker 的首次加载成本挪到服务启动期。

后台线程执行，不阻塞健康检查；失败仅告警，不影响服务可用性。
"""

from __future__ import annotations

import logging

from backend.config import settings

logger = logging.getLogger(__name__)


def warmup_models() -> None:
    """加载 Embedding 模型；启用 Reranker 时一并加载。"""
    from backend.services.embedding import embed_texts

    try:
        embed_texts(["预热"])
    except Exception as error:
        logger.warning("Embedding 预热失败: %s", type(error).__name__)

    if not settings.RERANKER_ENABLED:
        return
    from backend.services.reranker import rerank

    try:
        rerank(
            "预热",
            [{"chunk_id": "warmup", "content": "预热", "metadata": {}}],
        )
    except Exception as error:
        logger.warning("Reranker 预热失败: %s", type(error).__name__)


def start_background_warmup() -> None:
    """服务启动时异步预热；关闭开关时不执行。"""
    if not settings.PREWARM_MODELS:
        return
    import threading

    thread = threading.Thread(target=warmup_models, daemon=True)
    thread.start()
