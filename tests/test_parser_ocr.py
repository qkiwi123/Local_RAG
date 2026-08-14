"""Parser 共用的视觉包装测试：失败短期记忆与成功缓存优先。"""

import pytest

from backend.models.document_structure import SourceLocator
from backend.services.parsers import ocr
from backend.services.vision import VisionError
from backend.services.vision_cache import VisionBudget


def _locator():
    return SourceLocator(
        kind="page", unit_index=0, location_label="第 1 页", page_number=1
    )


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch, temp_dir):
    monkeypatch.setattr(
        "backend.config.settings.VISION_CACHE_DIR", f"{temp_dir}/vision_cache"
    )
    monkeypatch.setattr(
        "backend.config.settings.VISION_MODEL", "glm-4.6v-flash"
    )
    ocr.clear_failure_memo()
    yield
    ocr.clear_failure_memo()


def test_failed_call_is_remembered_and_not_retried(monkeypatch):
    calls = []

    def failing(image_bytes):
        calls.append(image_bytes)
        raise VisionError("限流")

    warnings = []
    budget = VisionBudget(limit=10)

    first = ocr.run_vision(
        "file-hash",
        "pdf-page:1",
        b"image",
        failing,
        budget=budget,
        warnings=warnings,
        locator=_locator(),
        code="ocr_page_failed",
    )
    second = ocr.run_vision(
        "file-hash",
        "pdf-page:1",
        b"image",
        failing,
        budget=budget,
        warnings=warnings,
        locator=_locator(),
        code="ocr_page_failed",
    )

    assert first is None
    assert second is None
    assert len(calls) == 1  # 失败被短期记忆，不再重试
    assert len(warnings) == 1
    assert warnings[0].code == "ocr_page_failed"


def test_failure_memo_is_scoped_by_object_and_model(monkeypatch):
    calls = []

    def failing(image_bytes):
        calls.append(image_bytes)
        raise VisionError("限流")

    warnings = []
    budget = VisionBudget(limit=10)
    for object_id in ("pdf-page:1", "pdf-page:2"):
        ocr.run_vision(
            "file-hash",
            object_id,
            b"image",
            failing,
            budget=budget,
            warnings=warnings,
            locator=_locator(),
            code="ocr_page_failed",
        )

    assert len(calls) == 2  # 不同对象分别记忆


def test_successful_result_is_cached_and_preferred():
    warnings = []
    budget = VisionBudget(limit=10)

    first = ocr.run_vision(
        "file-hash",
        "pdf-page:1",
        b"image",
        lambda image_bytes: "识别文本",
        budget=budget,
        warnings=warnings,
        locator=_locator(),
        code="ocr_page_failed",
    )
    second = ocr.run_vision(
        "file-hash",
        "pdf-page:1",
        b"image",
        lambda image_bytes: (_ for _ in ()).throw(AssertionError("不应调用")),
        budget=budget,
        warnings=warnings,
        locator=_locator(),
        code="ocr_page_failed",
    )

    assert first == "识别文本"
    assert second == "识别文本"
    assert warnings == []


def test_clear_failure_memo_allows_retry(monkeypatch):
    calls = []

    def failing(image_bytes):
        calls.append(image_bytes)
        raise VisionError("限流")

    budget = VisionBudget(limit=10)
    args = dict(
        budget=budget,
        warnings=[],
        locator=_locator(),
        code="ocr_page_failed",
    )
    ocr.run_vision("h", "o", b"image", failing, **args)
    ocr.clear_failure_memo()
    ocr.run_vision("h", "o", b"image", failing, **args)

    assert len(calls) == 2
