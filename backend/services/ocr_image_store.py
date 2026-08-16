"""OCR 原图文件的写入与读取。

图片按内容地址命名：image_ref = sha256(file_sha256 + object_id)[:16]。
写入是幂等的（存在即复用），失败只返回 None，不阻断文档入库；
读取只允许纯十六进制 ref，避免路径穿越。
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from backend.config import settings

_REF_RE = re.compile(r"^[0-9a-f]{16}$")


def reference_for(file_hash: str, object_id: str) -> str:
    raw = f"{file_hash}\n{object_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def save_image(
    file_hash: str,
    object_id: str,
    image_bytes: bytes,
) -> str | None:
    """保存 OCR 原图并返回 image_ref；开关关闭或写入失败返回 None。"""
    if not settings.OCR_IMAGE_PREVIEW_ENABLED or not image_bytes:
        return None

    image_ref = reference_for(file_hash, object_id)
    target = Path(settings.OCR_IMAGE_DIR) / f"{image_ref}.jpg"
    if target.is_file():
        return image_ref

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".jpg.tmp")
        temporary.write_bytes(image_bytes)
        os.replace(temporary, target)
        return image_ref
    except OSError:
        return None


def resolve_image(image_ref: str) -> Path | None:
    """按 ref 返回图片路径；非法 ref 或文件不存在返回 None。"""
    if not _REF_RE.match(image_ref or ""):
        return None
    path = Path(settings.OCR_IMAGE_DIR) / f"{image_ref}.jpg"
    return path if path.is_file() else None


def delete_image(image_ref: str) -> None:
    """删除单个图片文件；失败静默（清理只是尽力而为）。"""
    path = resolve_image(image_ref)
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        pass
