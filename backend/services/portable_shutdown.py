"""Portable shutdown drains each resource and records failures independently."""
import asyncio
import logging

logger = logging.getLogger(__name__)


async def drain(warmup_thread=None) -> dict:
    from backend.services import sparse_rebuild, sparse_retriever

    failures = []
    try:
        while not (await asyncio.to_thread(sparse_rebuild.stop_worker, timeout=1.0))["stopped"]:
            await asyncio.sleep(0.1)
    except Exception:
        failures.append("sparse_worker")
        logger.exception("便携退出：停止Sparse任务失败")
    try:
        sparse_retriever.clear_cache()
    except Exception:
        failures.append("sparse_cache")
        logger.exception("便携退出：释放Sparse缓存失败")
    try:
        while warmup_thread is not None and warmup_thread.is_alive():
            await asyncio.to_thread(warmup_thread.join, 1.0)
    except Exception:
        failures.append("model_warmup")
        logger.exception("便携退出：等待模型预热失败")
    result = {"status": "failed" if failures else "completed", "failed_steps": failures}
    if failures:
        logger.error("便携退出清理未全部完成：%s；请保留日志，重启前检查数据", failures)
    else:
        logger.info("便携退出：后台任务及模型预热已结束，Sparse缓存已释放")
    return result
