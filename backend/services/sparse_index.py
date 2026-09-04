"""持久化词法索引的稳定协议与显式错误类型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, Sequence


class SparseIndexStatus(str, Enum):
    READY = "ready"
    DIRTY = "dirty"
    MISSING = "missing"
    CORRUPT = "corrupt"
    MISMATCH = "mismatch"


class SparseIndexError(RuntimeError):
    """持久化Sparse索引错误基类。"""


class SparseIndexMissingError(SparseIndexError):
    """索引目录、活动指针或必需文件不存在。"""


class SparseIndexCorruptError(SparseIndexError):
    """文件损坏、hash不符或内部数组合同无效。"""


class SparseIndexMismatchError(SparseIndexError):
    """索引与当前语料、分词器或BM25参数不匹配。"""


class SparseIndexDirtyError(SparseIndexError):
    """活动索引已因语料变化标记为dirty。"""


class SparseIndexBuildError(SparseIndexError):
    """输入语料或构建参数不满足索引合同。"""


@dataclass(frozen=True)
class SparseHit:
    """不携带正文的词法命中；P3再按chunk_id批量回取事实数据。"""

    chunk_id: str
    score: float
    rank: int
    ordinal: int


class SparseIndexReader(Protocol):
    generation_id: str
    manifest_hash: str

    def search_tokens(self, tokens: Sequence[str], top_k: int) -> list[SparseHit]: ...

    def close(self) -> None: ...
