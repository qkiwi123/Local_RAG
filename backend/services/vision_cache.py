"""视觉识别结果的磁盘缓存与单文档调用预算。

缓存键 = 文件 sha256 + 对象标识（页码/图片哈希）+ 模型版本，重建索引时
命中缓存不会重复计费。缓存写入使用临时文件 + 原子替换。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from backend.config import settings


class VisionBudgetExceeded(RuntimeError):
    """单文档视觉调用次数超过上限。"""


class VisionBudget:
    """限制单文档的视觉调用次数（OCR 页 + 图片合计）。"""

    def __init__(self, limit: int | None = None):
        self.limit = (
            settings.OCR_MAX_PER_DOCUMENT if limit is None else limit
        )
        self.used = 0

    def consume(self) -> None:
        if self.used >= self.limit:
            raise VisionBudgetExceeded(
                f"视觉调用次数超过单文档上限（{self.limit}）"
            )
        self.used += 1


def cache_key(file_hash: str, object_id: str, model: str) -> str:
    raw = f"{file_hash}\n{object_id}\n{model}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_path(file_hash: str, object_id: str, model: str) -> Path:
    return (
        Path(settings.VISION_CACHE_DIR)
        / f"{cache_key(file_hash, object_id, model)}.txt"
    )


def get_cached(file_hash: str, object_id: str, model: str) -> str | None:
    """命中返回缓存文本；未命中或损坏返回 None。"""
    try:
        path = _cache_path(file_hash, object_id, model)
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8").strip()
        return text or None
    except (OSError, UnicodeDecodeError):
        return None


def put_cached(
    file_hash: str,
    object_id: str,
    model: str,
    text: str,
) -> None:
    """原子写入缓存；失败不抛出（缓存只是加速，不应影响入库）。"""
    try:
        root = Path(settings.VISION_CACHE_DIR)
        root.mkdir(parents=True, exist_ok=True)
        target = _cache_path(file_hash, object_id, model)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, target)
    except OSError:
        pass
