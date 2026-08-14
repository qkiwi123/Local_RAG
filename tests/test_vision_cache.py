"""视觉缓存与调用预算测试（隔离临时缓存目录）。"""

import pytest

from backend.config import settings
from backend.services.vision_cache import (
    VisionBudget,
    VisionBudgetExceeded,
    cache_key,
    get_cached,
    put_cached,
)


@pytest.fixture(autouse=True)
def isolated_cache_dir(monkeypatch, temp_dir):
    monkeypatch.setattr(settings, "VISION_CACHE_DIR", f"{temp_dir}/vision_cache")
    monkeypatch.setattr(settings, "VISION_MODEL", "mimo-v2.5")


def test_put_and_get_roundtrip():
    assert get_cached("file-hash", "pdf-page:1", "mimo-v2.5") is None
    put_cached("file-hash", "pdf-page:1", "mimo-v2.5", "识别文本")
    assert get_cached("file-hash", "pdf-page:1", "mimo-v2.5") == "识别文本"


def test_cache_key_isolated_by_hash_object_and_model():
    base = cache_key("h", "o", "m")
    assert cache_key("h", "o", "m") == base
    assert cache_key("h2", "o", "m") != base
    assert cache_key("h", "o2", "m") != base
    assert cache_key("h", "o", "m2") != base


def test_corrupted_cache_file_returns_none():
    put_cached("h", "o", "m", "文本")
    target = (
        pytest.importorskip("pathlib").Path(settings.VISION_CACHE_DIR)
        / f"{cache_key('h', 'o', 'm')}.txt"
    )
    target.write_bytes(b"\xff\xfe\x00broken")
    assert get_cached("h", "o", "m") is None


def test_budget_consumes_and_exceeds(monkeypatch):
    monkeypatch.setattr(settings, "OCR_MAX_PER_DOCUMENT", 2)
    budget = VisionBudget()
    budget.consume()
    budget.consume()
    with pytest.raises(VisionBudgetExceeded, match="上限"):
        budget.consume()


def test_budget_uses_configured_limit(monkeypatch):
    monkeypatch.setattr(settings, "OCR_MAX_PER_DOCUMENT", 5)
    assert VisionBudget().limit == 5
    assert VisionBudget(limit=3).limit == 3
