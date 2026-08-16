"""OCR 原图存储单元测试（不依赖真实视觉模型）。"""

import pytest

from backend.config import settings
from backend.services import ocr_image_store


@pytest.fixture
def image_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OCR_IMAGE_PREVIEW_ENABLED", True)
    monkeypatch.setattr(settings, "OCR_IMAGE_DIR", str(tmp_path / "ocr_images"))
    return tmp_path / "ocr_images"


def test_save_is_idempotent_and_reference_is_deterministic(image_dir):
    ref = ocr_image_store.save_image("file-hash", "pdf-page:1", b"jpeg-bytes")

    assert ref is not None
    assert len(ref) == 16
    assert (image_dir / f"{ref}.jpg").read_bytes() == b"jpeg-bytes"
    assert ocr_image_store.save_image("file-hash", "pdf-page:1", b"jpeg-bytes") == ref


def test_different_object_ids_get_different_refs(image_dir):
    first = ocr_image_store.save_image("file-hash", "pdf-page:1", b"a")
    second = ocr_image_store.save_image("file-hash", "pdf-page:2", b"a")

    assert first != second


def test_resolve_rejects_path_traversal_and_missing_files(image_dir):
    assert ocr_image_store.resolve_image("../etc/passwd") is None
    assert ocr_image_store.resolve_image("not-a-hex-ref") is None
    assert ocr_image_store.resolve_image("0" * 16) is None


def test_disabled_store_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "OCR_IMAGE_PREVIEW_ENABLED", False)
    monkeypatch.setattr(settings, "OCR_IMAGE_DIR", str(tmp_path / "ocr_images"))

    assert ocr_image_store.save_image("file-hash", "pdf-page:1", b"x") is None


def test_delete_image_removes_file(image_dir):
    ref = ocr_image_store.save_image("file-hash", "pdf-page:1", b"jpeg-bytes")

    assert ocr_image_store.resolve_image(ref) is not None
    ocr_image_store.delete_image(ref)
    assert ocr_image_store.resolve_image(ref) is None
