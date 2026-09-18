"""Parser 共用的视觉识别包装：文件哈希、预算、缓存、warning 与属性标记。

pdf / docx / pptx adapter 统一走这里的预算与缓存语义：
- 命中缓存不计入预算（不产生 API 调用）；
- 失败与超预算只追加 ParseWarning，不阻塞入库；
- 视觉产出 block 统一带 ocr/vision_model/vision_kind 属性。
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time

from backend.config import settings
from backend.models.document_structure import ParseWarning, SourceLocator
from backend.services.vision import VisionError, VisionCallConfig
from backend.services.vision_cache import (
    VisionBudget,
    VisionBudgetExceeded,
    get_cached,
    put_cached,
)

logger = logging.getLogger(__name__)

# 失败短期记忆：同一进程内相同的 (file_hash, object_id) 失败后，在 TTL 内
# 不再重复调用（批量重建时同一图片会在多页/多文档重复出现，失败重试会
# 造成请求风暴）。成功结果走磁盘缓存，不受此影响。
_FAILURE_TTL_SECONDS = 600.0
_failure_lock = threading.Lock()
_failure_memo: dict[str, tuple[float, str]] = {}


def _memo_key(file_hash: str, object_id: str, model: str) -> str:
    return f"{file_hash}\n{object_id}\n{model}"


def _recently_failed(key: str) -> bool:
    with _failure_lock:
        failure = _failure_memo.get(key)
        if failure is None:
            return False
        failed_at, _message = failure
        if time.monotonic() - failed_at > _FAILURE_TTL_SECONDS:
            _failure_memo.pop(key, None)
            return False
        return True


def _remember_failure(key: str, message: str = "视觉调用失败") -> None:
    with _failure_lock:
        _failure_memo[key] = (time.monotonic(), message)


def clear_failure_memo() -> None:
    """清空失败记忆（测试与显式维护使用）。"""
    with _failure_lock:
        _failure_memo.clear()


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


def ocr_attributes(kind: str, config: VisionCallConfig | None = None) -> dict[str, str | int | bool]:
    return {
        "ocr": True,
        "vision_model": config.model if config else settings.VISION_MODEL,
        "vision_kind": kind,
        **({"vision_settings_revision": config.revision} if config else {}),
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
    config: VisionCallConfig | None = None,
    task: str = "recognize",
) -> str | None:
    """预算 + 缓存 + 失败记忆 + 调用 + warning 的公共包装；失败返回 None。"""
    if config is not None and not config.options.enabled:
        return None
    model_key = settings.VISION_MODEL if config is None else (
        config.fingerprint(task) + ":" + hashlib.sha256(image_bytes).hexdigest()
    )
    memo_key = _memo_key(file_hash, object_id, model_key)
    if config is not None:
        memo_key += ":" + config.credential_fingerprint() + ":" + str(getattr(config.options, f"{task}_timeout_seconds"))
    cached = get_cached(file_hash, object_id, model_key)
    if cached is not None:
        return cached
    if _recently_failed(memo_key):
        if config is not None:
            with _failure_lock:
                message = _failure_memo.get(memo_key, (0, "视觉调用失败"))[1]
            warnings.append(ParseWarning(code=code, message="近期识别失败，暂未重复调用：" + message, locator=locator))
        return None
    try:
        budget.consume()
        text = call(image_bytes, config=config) if config is not None else call(image_bytes)
    except VisionBudgetExceeded as error:
        warnings.append(
            ParseWarning(code=code, message=str(error), locator=locator)
        )
        return None
    except VisionError as error:
        _remember_failure(memo_key, str(error))
        logger.warning("Vision failure: object=%s model=%s code=%s status=%s request_id=%s", object_id,
                       config.model if config else settings.VISION_MODEL,
                       error.code, error.status_code, error.request_id)
        warnings.append(
            ParseWarning(code=code, message=str(error), locator=locator)
        )
        return None
    except Exception as error:
        _remember_failure(memo_key)
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
    put_cached(file_hash, object_id, model_key, text)
    return text
