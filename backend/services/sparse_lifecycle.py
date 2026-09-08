"""Sparse持久化索引的生命周期护栏。

P6-A不变量：任何Chroma语料变更生效前，活动Sparse指针必须已经持久标为
dirty；标脏失败则拒绝本次数据变更。dirty状态落在磁盘active.json上，
服务重启后依然生效，不依赖进程内revision计数。
"""

from __future__ import annotations

from pathlib import Path
from contextlib import contextmanager
from functools import wraps
import logging
import threading
from typing import Any

from backend.config import settings
from backend.services.sparse_index import SparseIndexCorruptError

# 单服务进程内，文档事务、源快照和最终发布共用一把锁；构建本身不持锁。
# 锁顺序固定为 lifecycle -> sparse_retriever -> rebuild condition。
corpus_lock = threading.RLock()
_local = threading.local()


@contextmanager
def document_transaction(reason: str):
    """覆盖文档的Chroma、SQLite提交及失败补偿，最外层结束后再排队。"""
    with corpus_lock:
        depth = getattr(_local, "depth", 0)
        _local.depth = depth + 1
        try:
            yield
        finally:
            _local.depth = depth
            if depth == 0:
                try:
                    from backend.services import sparse_rebuild

                    sparse_rebuild.request_rebuild(reason)
                except Exception:
                    logging.getLogger(__name__).exception("Sparse重建排队失败")


def serialized(function):
    """同步查询/状态读取与语料写入、指针切换互斥。"""
    @wraps(function)
    def wrapped(*args, **kwargs):
        with corpus_lock:
            return function(*args, **kwargs)
    return wrapped


def transaction(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with document_transaction(function.__name__):
            return function(*args, **kwargs)
    return wrapped


def active_root(root: str | Path | None = None) -> Path:
    """返回Sparse索引根目录；默认取部署配置。"""
    return Path(root).resolve() if root is not None else Path(
        settings.SPARSE_INDEX_ROOT
    ).resolve()


def read_active_status(root: str | Path | None = None) -> str | None:
    """返回活动指针状态（ready/dirty）；无指针返回None，损坏时显式报错。

    延迟导入bm25s_store，避免vector_store等轻量调用方拖入numpy/jieba。
    """
    from backend.services import bm25s_store

    payload = bm25s_store.read_active_payload(active_root(root))
    if payload is None:
        return None
    status = payload.get("status")
    if status not in {"ready", "dirty"}:
        raise SparseIndexCorruptError(f"active.json状态无效：{status!r}")
    return str(status)


@serialized
def mark_corpus_changing(
    reason: str, *, root: str | Path | None = None
) -> dict[str, Any] | None:
    """在修改Chroma前持久标脏活动Sparse指针。

    - 无active.json：没有活动索引需要失效，直接放行（返回None）；
    - 指针已dirty：幂等成功，刷新dirty原因；
    - 指针存在但标脏失败（磁盘错误、指针损坏等）：抛异常，调用方必须
      拒绝本次数据变更，绝不出现"数据已变、索引仍旧"却无人知晓的状态。
    """
    from backend.services import bm25s_store

    target = active_root(root)
    payload = bm25s_store.read_active_payload(target)
    if payload is None:
        return None
    return bm25s_store.mark_active_dirty(target, reason)
