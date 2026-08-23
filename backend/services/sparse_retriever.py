"""应用侧中文 BM25 检索：从 Chroma chunk 快照惰性构建内存索引。"""

from __future__ import annotations

import re
import threading
import unicodedata

import jieba
from rank_bm25 import BM25Okapi

from backend.config import settings
from backend.services import vector_store


_TOKEN_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9_.+%\-]+")
_HAS_WORD_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")
_lock = threading.Lock()
_cache = {
    "key": None,
    "chunks": [],
    "model": None,
}


def tokenize(text: str) -> list[str]:
    """NFKC 规范化后使用 jieba 精确模式切词，并移除纯标点。"""
    normalized = unicodedata.normalize("NFKC", text or "").lower()
    return [
        token
        for part in _TOKEN_RE.findall(normalized)
        for token in jieba.lcut(part, cut_all=False)
        if token.strip() and _HAS_WORD_RE.search(token)
    ]


def clear_cache() -> None:
    """清空进程内 BM25 索引，主要供测试和显式维护使用。"""
    with _lock:
        _cache.update(key=None, chunks=[], model=None)


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
) -> list[dict]:
    """返回 BM25 score 大于 0 的 Top K chunk，分数越大越相关。

    allowed_document_ids 为 None 时全库检索，否则只检索这些文档。
    """
    if top_k <= 0 or not query.strip():
        return []

    chunks, model = _get_index(allowed_document_ids, k1=k1, b=b)
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
