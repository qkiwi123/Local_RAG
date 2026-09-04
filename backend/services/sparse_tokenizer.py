"""无存储副作用的中文Sparse分词合同。"""

from __future__ import annotations

import re
import unicodedata

import jieba


TOKENIZER_CONTRACT = "nfkc-lower-regex-jieba-exact-v1"
TOKEN_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9_.+%\-]+")
HAS_WORD_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")


def tokenize(text: str) -> list[str]:
    """NFKC规范化后使用jieba精确模式切词，并移除纯标点。"""
    normalized = unicodedata.normalize("NFKC", text or "").lower()
    return [
        token
        for part in TOKEN_RE.findall(normalized)
        for token in jieba.lcut(part, cut_all=False)
        if token.strip() and HAS_WORD_RE.search(token)
    ]
