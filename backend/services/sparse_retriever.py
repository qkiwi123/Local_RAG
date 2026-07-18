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


def _get_index() -> tuple[list[dict], BM25Okapi | None]:
    key = (
        vector_store.get_index_revision(),
        settings.BM25_K1,
        settings.BM25_B,
    )
    with _lock:
        if _cache["key"] != key:
            chunks = vector_store.get_all_chunks()
            corpus = [tokenize(row["content"]) for row in chunks]
            model = (
                BM25Okapi(corpus, k1=settings.BM25_K1, b=settings.BM25_B)
                if corpus
                else None
            )
            _cache.update(key=key, chunks=chunks, model=model)
        return _cache["chunks"], _cache["model"]


def search(query: str, top_k: int) -> list[dict]:
    """返回 BM25 score 大于 0 的 Top K chunk，分数越大越相关。"""
    if top_k <= 0 or not query.strip():
        return []

    chunks, model = _get_index()
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
