"""Parser 共用的视觉识别包装：文件哈希、预算、缓存、warning 与属性标记。

pdf / docx / pptx adapter 统一走这里的预算与缓存语义：
- 命中缓存不计入预算（不产生 API 调用）；
- 失败与超预算只追加 ParseWarning，不阻塞入库；
- 视觉产出 block 统一带 ocr/vision_model/vision_kind 属性。
"""

from __future__ import annotations

import hashlib
import logging

from backend.config import settings
from backend.models.document_structure import ParseWarning, SourceLocator
from backend.services.vision import VisionError
from backend.services.vision_cache import (
    VisionBudget,
    VisionBudgetExceeded,
    get_cached,
    put_cached,
)

logger = logging.getLogger(__name__)


def file_sha256(file_path: str) -> str:
    """流式计算文件 sha256，供缓存键与图片对象标识使用。"""
    digest = hashlib.sha256()
    with open(file_path, "rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def image_blob_sha256(blob: bytes) -> str:
    """内嵌图片内容的 sha256（缓存键按内容去重）。"""
    return hashlib.sha256(blob).hexdigest()


def ocr_attributes(kind: str) -> dict[str, str | int | bool]:
    return {
        "ocr": True,
        "vision_model": settings.VISION_MODEL,
        "vision_kind": kind,
    }


def run_vision(
    file_hash: str,
    object_id: str,
    image_bytes: bytes,
    call,
    *,
    budget: VisionBudget,
    warnings: list[ParseWarning],
    locator: SourceLocator,
    code: str,
) -> str | None:
    """预算 + 缓存 + 调用 + warning 的公共包装；失败返回 None。"""
    cached = get_cached(file_hash, object_id, settings.VISION_MODEL)
    if cached is not None:
        return cached
    try:
        budget.consume()
        text = call(image_bytes)
    except VisionBudgetExceeded as error:
        warnings.append(
            ParseWarning(code=code, message=str(error), locator=locator)
        )
        return None
    except VisionError as error:
        warnings.append(
            ParseWarning(code=code, message=str(error), locator=locator)
        )
        return None
    except Exception as error:
        logger.warning(
            "Vision call failed: file_hash=%s object_id=%s error=%s",
            file_hash[:12],
            object_id,
            type(error).__name__,
        )
        warnings.append(
            ParseWarning(
                code=code,
                message=f"视觉识别失败: {type(error).__name__}",
                locator=locator,
            )
        )
        return None
    put_cached(file_hash, object_id, settings.VISION_MODEL, text)
    return text
