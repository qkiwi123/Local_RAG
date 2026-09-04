"""应用侧中文BM25：全库可选mmap generation，过滤范围保持旧语义。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import threading
from time import perf_counter
from typing import Any

import jieba
from rank_bm25 import BM25Okapi

from backend.config import settings
from backend.services import vector_store
from backend.services import bm25s_store
from backend.services.parsers.constants import PARSER_VERSION
from backend.services.sparse_index import (
    SparseIndexCorruptError,
    SparseIndexDirtyError,
    SparseIndexMismatchError,
    SparseIndexMissingError,
)
from backend.services.sparse_tokenizer import tokenize


_lock = threading.Lock()
_cache = {
    "key": None,
    "chunks": [],
    "model": None,
}
_persistent_cache: dict[str, Any] = {
    "key": None,
    "index": None,
}


def clear_cache() -> None:
    """清空内存BM25并关闭持久化mmap句柄。"""
    with _lock:
        _cache.update(key=None, chunks=[], model=None)
        loaded = _persistent_cache.get("index")
        if loaded is not None:
            loaded.close()
        _persistent_cache.update(key=None, index=None)


def _diagnostics(backend: str, **values: Any) -> dict[str, Any]:
    return {
        "sparse_backend": backend,
        "sparse_index_generation": None,
        "sparse_manifest_hash": None,
        "sparse_index_status": "ready",
        "sparse_fallback_reason": None,
        "sparse_index_load_ms": 0.0,
        "sparse_contract_version": bm25s_store.INDEX_FORMAT,
        **values,
    }


def _set_diagnostics(target: dict[str, Any] | None, values: dict[str, Any]) -> None:
    if target is not None:
        target.clear()
        target.update(values)


def _expected_contract(*, k1: float, b: float) -> dict[str, Any]:
    return {
        "corpus_id": settings.SPARSE_INDEX_CORPUS_ID,
        "tokenizer": {
            "contract": bm25s_store.TOKENIZER_CONTRACT,
            "jieba_version": jieba.__version__,
            "user_dictionary_sha256": None,
            "stopwords_sha256": None,
        },
        "chunking": {
            "parser_version": PARSER_VERSION,
            "strategy": settings.CHUNK_STRATEGY,
            "size": settings.CHUNK_SIZE,
            "overlap": settings.CHUNK_OVERLAP,
            "max_tokens": settings.CHUNK_MAX_TOKENS,
        },
        "bm25": {
            "implementation": "bm25s",
            "bm25s_version": bm25s_store.bm25s.__version__,
            "compatibility_oracle": "rank_bm25.BM25Okapi",
            "method": bm25s_store.METHOD,
            "idf_method": bm25s_store.IDF_METHOD,
            "k1": k1,
            "b": b,
            "epsilon": 0.25,
            "dtype": bm25s_store.DTYPE,
            "int_dtype": bm25s_store.INT_DTYPE,
            "backend": bm25s_store.BACKEND,
            "csc_backend": bm25s_store.CSC_BACKEND,
        },
    }


def _active_token(root: Path) -> str | None:
    try:
        payload = (root / "active.json").read_bytes()
    except FileNotFoundError:
        return None
    return hashlib.sha256(payload).hexdigest()


def _get_persistent_index(*, k1: float, b: float) -> tuple[Any, float]:
    root = Path(settings.SPARSE_INDEX_ROOT).resolve()
    contract = _expected_contract(k1=k1, b=b)
    key = (
        str(root),
        _active_token(root),
        json.dumps(contract, ensure_ascii=False, sort_keys=True),
    )
    with _lock:
        if _persistent_cache["key"] == key:
            return _persistent_cache["index"], 0.0
        previous = _persistent_cache.get("index")
        if previous is not None:
            previous.close()
        _persistent_cache.update(key=None, index=None)
        started = perf_counter()
        loaded = bm25s_store.load_active(root, expected_contract=contract)
        load_ms = (perf_counter() - started) * 1000
        _persistent_cache.update(key=key, index=loaded)
        return loaded, load_ms


def get_runtime_status() -> dict[str, Any]:
    """返回不扫描语料的Sparse运行状态；mmap首次检查会完成一次受控加载。"""
    backend = settings.SPARSE_BACKEND
    if backend == "rank_bm25_memory":
        return _diagnostics(
            backend,
            sparse_contract_version=bm25s_store.TOKENIZER_CONTRACT,
        )
    if vector_store.get_index_revision() != 0:
        return _diagnostics(backend, sparse_index_status="dirty")
    try:
        loaded, load_ms = _get_persistent_index(
            k1=float(settings.BM25_K1),
            b=float(settings.BM25_B),
        )
    except SparseIndexMissingError:
        return _diagnostics(backend, sparse_index_status="missing")
    except SparseIndexDirtyError:
        return _diagnostics(backend, sparse_index_status="dirty")
    except SparseIndexMismatchError:
        return _diagnostics(backend, sparse_index_status="mismatch")
    except SparseIndexCorruptError:
        return _diagnostics(backend, sparse_index_status="corrupt")
    return _diagnostics(
        backend,
        sparse_index_generation=loaded.generation_id,
        sparse_manifest_hash=loaded.manifest_hash,
        sparse_index_load_ms=round(load_ms, 3),
    )


def _get_index(
    allowed_document_ids: list[str] | None = None,
    *,
    k1: float | None = None,
    b: float | None = None,
) -> tuple[list[dict], BM25Okapi | None]:
    """按文档过滤集合构建 BM25 索引；不同过滤集各自缓存。

    allowed_document_ids 为 None 表示全库；空列表表示筛选范围为空。
    """
    effective_k1 = settings.BM25_K1 if k1 is None else k1
    effective_b = settings.BM25_B if b is None else b
    allowed_key = (
        None
        if allowed_document_ids is None
        else tuple(sorted(set(allowed_document_ids)))
    )
    key = (
        vector_store.get_index_revision(),
        effective_k1,
        effective_b,
        allowed_key,
    )
    with _lock:
        if _cache["key"] != key:
            chunks = vector_store.get_all_chunks()
            if allowed_document_ids is not None:
                allowed = set(allowed_document_ids)
                chunks = [
                    row
                    for row in chunks
                    if (row.get("metadata") or {}).get("document_id") in allowed
                ]
            corpus = [tokenize(row["content"]) for row in chunks]
            model = (
                BM25Okapi(corpus, k1=effective_k1, b=effective_b)
                if corpus
                else None
            )
            _cache.update(key=key, chunks=chunks, model=model)
        return _cache["chunks"], _cache["model"]


def search(
    query: str,
    top_k: int,
    allowed_document_ids: list[str] | None = None,
    *,
    k1: float | None = None,
    b: float | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> list[dict]:
    """返回 BM25 score 大于 0 的 Top K chunk，分数越大越相关。

    allowed_document_ids 为 None 时全库检索，否则只检索这些文档。
    """
    if top_k <= 0 or not query.strip():
        return []

    if allowed_document_ids is not None and not allowed_document_ids:
        return []

    effective_k1 = settings.BM25_K1 if k1 is None else k1
    effective_b = settings.BM25_B if b is None else b
    use_persistent = (
        settings.SPARSE_BACKEND == "bm25s_mmap"
        and allowed_document_ids is None
    )
    if use_persistent:
        if vector_store.get_index_revision() != 0:
            raise SparseIndexDirtyError(
                "当前进程已修改Chroma语料，持久化Sparse generation已失效"
            )
        loaded, load_ms = _get_persistent_index(
            k1=effective_k1,
            b=effective_b,
        )
        hits = loaded.search_tokens(tokenize(query), top_k)
        chunk_ids = [hit.chunk_id for hit in hits]
        chunks = vector_store.get_chunks_by_ids(chunk_ids)
        by_id = {row["chunk_id"]: row for row in chunks}
        missing = [chunk_id for chunk_id in chunk_ids if chunk_id not in by_id]
        if missing:
            raise SparseIndexMismatchError(
                f"持久化Sparse命中的chunk不在当前Chroma：{missing[0]}"
            )
        _set_diagnostics(
            diagnostics,
            _diagnostics(
                "bm25s_mmap",
                sparse_index_generation=loaded.generation_id,
                sparse_manifest_hash=loaded.manifest_hash,
                sparse_index_load_ms=round(load_ms, 3),
            ),
        )
        return [
            {
                **by_id[hit.chunk_id],
                "distance": None,
                "bm25_score": hit.score,
                "bm25_rank": hit.rank,
            }
            for hit in hits
        ]

    _set_diagnostics(
        diagnostics,
        _diagnostics(
            "rank_bm25_memory",
            sparse_contract_version=bm25s_store.TOKENIZER_CONTRACT,
        ),
    )
    chunks, model = _get_index(
        allowed_document_ids,
        k1=effective_k1,
        b=effective_b,
    )
    if model is None:
        return []

    scores = model.get_scores(tokenize(query))
    ranked = sorted(
        (
            (index, float(score))
            for index, score in enumerate(scores)
            if score > 0
        ),
        key=lambda item: (-item[1], chunks[item[0]]["chunk_id"]),
    )[:top_k]

    return [
        {
            **chunks[index],
            "distance": None,
            "bm25_score": score,
            "bm25_rank": rank,
        }
        for rank, (index, score) in enumerate(ranked, 1)
    ]
