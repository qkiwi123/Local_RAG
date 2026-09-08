"""Sparse索引后台重建协调器（P6-A重建协议）。

职责与边界：
- 单实例：每个进程至多一个worker线程；pending标记把连续触发的重建
  合并为一次构建。
- 绑定与复核：构建绑定开始时的语料revision与内容摘要；构建完成后
  重新取证，源发生变化则丢弃候选generation并重新排队，绝不发布
  过时索引。
- 只在显式部署SPARSE_BACKEND=bm25s_mmap且未关闭自动重建时工作；
  内存后端部署不构建、不发布任何索引。
- 查询线程永远不同步重建；重建失败按退避重试，不静默降级。
"""

from __future__ import annotations

import logging
import json
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

from backend.config import settings

logger = logging.getLogger(__name__)

# 每轮构建与mmap重载均使用仓库固定120题对比rank_bm25，不调用生成模型。
def _oracle_questions():
    path = Path(__file__).resolve().parents[2] / "eval/crud_rag_1000/v3_1/questions_v3_120q.json"
    questions = json.loads(path.read_text(encoding="utf-8"))["questions"]
    if len(questions) != 120 or any(not str(q.get("question", "")).strip() for q in questions):
        raise ValueError("后台重建要求固定120道有效oracle问题")
    return questions

_cv = threading.Condition()
_thread: threading.Thread | None = None
_pending = False
_running = False
_stop = False
_retry_at: float | None = None
_pending_reasons: list[str] = []
_last_result: dict[str, Any] = {"outcome": "idle", "at": None, "attempt": 0}
_backoff_s = 2.0
_attempt = 0
_provider: Callable[[], list[dict]] | None = None
_runtime_provider: Callable | None = None


def configure(
    *,
    provider: Callable[[], list[dict]] | None = None,
    backoff_s: float | None = None,
    runtime_provider: Callable | None = None,
) -> None:
    """启动时绑定有效配置读取器；provider/backoff用于隔离测试。"""
    global _provider, _backoff_s, _runtime_provider
    with _cv:
        if provider is not None:
            _provider = provider
        if backoff_s is not None:
            _backoff_s = float(backoff_s)
        if runtime_provider is not None:
            _runtime_provider = runtime_provider


def reset_for_tests() -> None:
    """恢复模块初始状态；仅测试使用。"""
    global _thread, _pending, _running, _stop, _retry_at
    global _last_result, _backoff_s, _attempt, _provider, _runtime_provider
    with _cv:
        if _thread is not None and _thread.is_alive():
            raise RuntimeError("必须先停止worker才能重置")
        _thread = None
        _pending = False
        _running = False
        _stop = False
        _retry_at = None
        _pending_reasons.clear()
        _last_result = {"outcome": "idle", "at": None, "attempt": 0}
        _backoff_s = 2.0
        _attempt = 0
        _provider = None
        _runtime_provider = None


def _contract():
    from backend.services import sparse_retriever

    retrieval = _runtime_provider().retrieval if _runtime_provider else None
    return sparse_retriever.expected_contract(
        k1=float(retrieval.bm25_k1 if retrieval else settings.BM25_K1),
        b=float(retrieval.bm25_b if retrieval else settings.BM25_B),
    )


def _chunks_provider() -> list[dict]:
    if _provider is not None:
        return _provider()
    from backend.services import vector_store

    chunks = vector_store.get_all_chunks()
    connection = sqlite3.connect(
        Path(settings.METADATA_DB_PATH).resolve().as_uri() + "?mode=ro", uri=True
    )
    try:
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise RuntimeError("SQLite quick_check未通过")
        expected = dict(connection.execute(
            "SELECT document_id, chunk_count FROM documents WHERE status = 'ready'"
        ).fetchall())
    finally:
        connection.close()
    actual = {}
    for row in chunks:
        document_id = str(row["metadata"].get("document_id") or "")
        actual[document_id] = actual.get(document_id, 0) + 1
    if actual != expected:
        raise RuntimeError("Chroma与SQLite ready文档及逐文档chunk数量不一致，保留dirty")
    return chunks


def _enabled() -> bool:
    return (
        settings.SPARSE_BACKEND == "bm25s_mmap"
        and bool(getattr(settings, "SPARSE_AUTO_REBUILD", True))
    )


def request_rebuild(reason: str) -> dict[str, Any]:
    """请求一次后台全量重建；连续请求合并为一代，不会重复排队。"""
    global _pending, _retry_at
    with _cv:
        if not _enabled():
            return {"triggered": False, "reason": "sparse_auto_rebuild_disabled"}
        _pending = True
        _pending_reasons.append(reason)
        del _pending_reasons[:-20]
        _retry_at = None
        _cv.notify_all()
        return {"triggered": True, "reason": reason}


def start_worker(*, recovery: bool = True) -> dict[str, Any]:
    """启动单实例worker；活动指针异常时触发启动期自愈重建。"""
    global _thread, _stop
    with _cv:
        if not _enabled():
            return {"started": False, "reason": "sparse_auto_rebuild_disabled"}
        _stop = False
        if _thread is not None and _thread.is_alive():
            started = False
        else:
            _thread = threading.Thread(
                target=_worker_loop, name="sparse-rebuild", daemon=True
            )
            _thread.start()
            started = True
    if started and recovery:
        _recover_existing_pointer()
    return {"started": started}


def _recover_existing_pointer() -> None:
    """启动期识别异常指针：dirty或一致性错配时自愈重建。

    无活动指针时不做引导构建——首个生产generation必须走受控构建入口。
    """
    from backend.services import bm25s_store, sparse_lifecycle

    try:
        status = sparse_lifecycle.read_active_status()
    except Exception as error:  # noqa: BLE001 自愈不能阻断启动
        logger.warning("Sparse活动指针不可读，跳过启动自愈：%s", error)
        return
    if status in {"ready", "dirty"}:
        # 重启校验完整摘要，计数相同不能证明正文或metadata相同。
        sparse_lifecycle.mark_corpus_changing("startup_verification")
        request_rebuild("startup_recovery:" + status)


def stop_worker(*, timeout: float = 10.0) -> dict[str, Any]:
    """请求worker停止并等待退出；用于测试与进程关闭。"""
    global _stop
    with _cv:
        _stop = True
        _cv.notify_all()
        thread = _thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=timeout)
    with _cv:
        alive = _thread is not None and _thread.is_alive()
    return {"stopped": not alive}


def wait_for_idle(timeout: float = 30.0) -> bool:
    """阻塞直到没有排队且没有正在执行的重建（测试辅助）。"""
    deadline = time.monotonic() + timeout
    with _cv:
        while _pending or _running:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            _cv.wait(timeout=min(remaining, 0.05))
        return True


def get_rebuild_status() -> dict[str, Any]:
    """返回协调器快照；P6-C观察窗口可将其接入状态接口。"""
    with _cv:
        thread = _thread
        return {
            "enabled": _enabled(),
            "thread_alive": bool(thread is not None and thread.is_alive()),
            "pending": _pending,
            "running": _running,
            "attempt": _attempt,
            "backoff_s": _backoff_s,
            "pending_reasons": list(_pending_reasons),
            "last_result": dict(_last_result),
        }


def _worker_loop() -> None:
    global _pending, _running, _retry_at, _attempt, _last_result
    while True:
        with _cv:
            while True:
                if _stop:
                    return
                if _pending and (_retry_at is None or time.monotonic() >= _retry_at):
                    break
                _cv.wait(timeout=0.1)
            _pending = False
            _running = True
        try:
            outcome = _run_cycle()
        except Exception as error:  # noqa: BLE001 重建失败不能杀死worker
            _attempt += 1
            _last_result = {
                "outcome": "failed",
                "at": time.time(),
                "attempt": _attempt,
                "error": f"{type(error).__name__}: {error}",
            }
            logger.exception("Sparse后台重建失败，将在退避后重试")
            with _cv:
                _retry_at = time.monotonic() + min(
                    _backoff_s * (2 ** min(max(_attempt - 1, 0), 10)), 60.0
                )
                _pending = True
        else:
            _last_result = {**outcome, "attempt": _attempt}
            if outcome.get("outcome") in {"published", "already_fresh", "empty"}:
                with _cv:
                    _attempt = 0
                    _retry_at = None
        finally:
            with _cv:
                _running = False
                _cv.notify_all()


def _fresh_active_generation(
    root: Path, contract: dict[str, Any], digest: str, metadata_digest: str
) -> str | None:
    """活动generation已与当前语料和合同一致时返回其ID，否则None。"""
    from backend.services import bm25s_store

    active = bm25s_store.read_active_payload(root)
    if not active:
        return None
    generation_id = bm25s_store._safe_generation_id(str(active.get("generation_id") or ""))
    try:
        candidate = bm25s_store.load_generation(
            root / "generations" / generation_id,
            expected_contract=contract,
        )
    except Exception:  # noqa: BLE001 不可用即视为不新鲜
        return None
    try:
        corpus = candidate.manifest.get("corpus") or {}
        if (str(corpus.get("content_sha256")) != digest
                or str(corpus.get("metadata_sha256")) != metadata_digest):
            return None
    finally:
        candidate.close()
    return generation_id


def _run_cycle() -> dict[str, Any]:
    global _pending
    from backend.services import bm25s_store, sparse_retriever, sparse_lifecycle
    from backend.services.sparse_tokenizer import tokenize

    if not _enabled():
        return {"outcome": "skipped_backend"}
    root = Path(settings.SPARSE_INDEX_ROOT).resolve()
    with _cv:
        reason = ";".join(_pending_reasons) or "unspecified"
        _pending_reasons.clear()
    with sparse_lifecycle.corpus_lock:
        # 首次引导必须走受控CLI，上传事件不能绕过影子代验收。
        if bm25s_store.read_active_payload(root) is None:
            return {"outcome": "needs_initial_generation", "at": time.time()}
        bound_revision = _vector_revision()
        chunks = _chunks_provider()
        contract = _contract()
        if not chunks:
            sparse_lifecycle.mark_corpus_changing("empty_corpus", root=root)
            return {"outcome": "empty", "at": time.time(), "reason": reason}
        digest = bm25s_store.corpus_content_digest(chunks)
        metadata_digest = bm25s_store.corpus_metadata_digest(chunks)
        fresh_generation = _fresh_active_generation(root, contract, digest, metadata_digest)
        if fresh_generation is not None:
            with _cv:
                if _stop or not _enabled():
                    return {"outcome": "stopped", "at": time.time()}
                # 即使复用也重新校验合同；设置保存与此区间同锁。
                if _vector_revision() != bound_revision or _contract() != contract:
                    _pending = True
                    return {"outcome": "superseded", "at": time.time()}
                bm25s_store.activate_generation(root, fresh_generation, allow_replace=True)
                sparse_retriever.bind_process_revision(bound_revision)
            return {
                "outcome": "already_fresh", "at": time.time(),
                "reason": reason, "generation_id": fresh_generation,
            }
        sparse_lifecycle.mark_corpus_changing("rebuilding", root=root)

    import uuid

    generation_id = f"auto-{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    build = bm25s_store.build_generation(
        chunks=chunks,
        target_root=root,
        corpus_id=contract["corpus_id"],
        tokenize=tokenize,
        tokenizer=contract["tokenizer"],
        chunking=contract["chunking"],
        source={
            "chroma_collection": "knowledge_base",
            "rebuild_reason": reason,
            "bound_revision": bound_revision,
        },
        k1=contract["bm25"]["k1"],
        b=contract["bm25"]["b"],
        generation_id=generation_id,
        publish=False,
        oracle_questions=_oracle_questions(),
    )

    # 复核、发布和进程绑定是同一个临界区，写入不能插在两者之间。
    with sparse_lifecycle.corpus_lock:
        current_chunks = _chunks_provider()
        superseded = (
            _vector_revision() != bound_revision
            or _contract() != contract
            or Path(settings.SPARSE_INDEX_ROOT).resolve() != root
            or not current_chunks
            or bm25s_store.corpus_content_digest(current_chunks) != digest
            or bm25s_store.corpus_metadata_digest(current_chunks) != metadata_digest
        )
        with _cv:
            stopped = _stop or not _enabled()
            if superseded or stopped:
                if not stopped:
                    _pending = True
                    _pending_reasons.append(f"superseded:{reason}")
                # 仅清理本轮在目标generations中创建的候选，保留历史活动代。
                candidate_dir = Path(build["generation_dir"]).resolve()
                expected_dir = root / "generations" / generation_id
                if candidate_dir != expected_dir or candidate_dir.is_symlink():
                    raise RuntimeError("拒绝清理非本轮候选目录")
                shutil.rmtree(candidate_dir)
                return {
                    "outcome": "stopped" if stopped else "superseded",
                    "at": time.time(), "reason": reason,
                    "generation_id": build["generation_id"],
                }
            candidate = bm25s_store.load_generation(build["generation_dir"], expected_contract=contract)
            candidate.close()
            bm25s_store.activate_generation(root, build["generation_id"], allow_replace=True)
            sparse_retriever.bind_process_revision(bound_revision)
    logger.info(
        "Sparse后台重建已发布 generation=%s chunks=%s",
        build["generation_id"],
        build["chunk_count"],
    )
    return {
        "outcome": "published",
        "at": time.time(),
        "reason": reason,
        "generation_id": build["generation_id"],
        "manifest_hash": build["manifest_hash"],
        "chunk_count": build["chunk_count"],
        "bound_revision": bound_revision,
    }


def _vector_revision() -> int:
    from backend.services import vector_store

    return vector_store.get_index_revision()
