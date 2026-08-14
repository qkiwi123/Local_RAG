"""视觉客户端测试：不发起真实网络请求，全部通过 mock 验证。"""

import pytest

from backend.config import settings
from backend.services import vision
from backend.services.vision import (
    VisionError,
    describe_image,
    normalize_image_bytes,
    recognize_text,
)


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeCompletions:
    def __init__(self, content="识别结果", calls=None):
        self._content = content
        self._calls = calls

    def create(self, **kwargs):
        if self._calls is not None:
            self._calls.append(kwargs)
        return type(
            "FakeResponse",
            (),
            {"choices": [_FakeChoice(self._content)]},
        )()


class _FakeChat:
    def __init__(self, content="识别结果", calls=None):
        self.completions = _FakeCompletions(content, calls)


class _FakeClient:
    def __init__(self, content="识别结果", calls=None):
        self.chat = _FakeChat(content, calls)


@pytest.fixture
def fake_client():
    calls = []
    monkeypatch_client = _FakeClient(calls=calls)
    return monkeypatch_client, calls


def test_recognize_text_sends_image_and_prompt(fake_client, monkeypatch):
    fake, calls = fake_client
    monkeypatch.setattr(settings, "VISION_API_KEY", "test-key")
    monkeypatch.setattr(settings, "VISION_BASE_URL", "https://vision.example/v1")
    monkeypatch.setattr(vision, "OpenAI", lambda **kwargs: fake)

    text = recognize_text(b"\xff\xd8fake-image")

    assert text == "识别结果"
    assert len(calls) == 1
    request = calls[0]
    assert request["model"] == settings.VISION_MODEL
    assert request["temperature"] == 0
    content = request["messages"][0]["content"]
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "转录图片中的全部文字" in content[1]["text"]


def test_describe_image_uses_description_prompt(fake_client, monkeypatch):
    fake, calls = fake_client
    monkeypatch.setattr(settings, "VISION_API_KEY", "test-key")
    monkeypatch.setattr(settings, "VISION_BASE_URL", "https://vision.example/v1")
    monkeypatch.setattr(vision, "OpenAI", lambda **kwargs: fake)

    describe_image(b"image-bytes")

    assert "仅描述图片/图表的内容与要点" in calls[0]["messages"][0]["content"][1]["text"]


def test_missing_configuration_raises_before_any_request(monkeypatch):
    monkeypatch.setattr(settings, "VISION_API_KEY", "")
    monkeypatch.setattr(settings, "VISION_BASE_URL", "")

    with pytest.raises(VisionError, match="未配置"):
        recognize_text(b"image")


def test_empty_model_reply_raises_vision_error(monkeypatch):
    monkeypatch.setattr(settings, "VISION_API_KEY", "test-key")
    monkeypatch.setattr(settings, "VISION_BASE_URL", "https://vision.example/v1")
    monkeypatch.setattr(
        vision, "OpenAI", lambda **kwargs: _FakeClient(content="   ")
    )

    with pytest.raises(VisionError, match="空文本"):
        recognize_text(b"image")


def test_ocr_switches_default_to_disabled():
    assert settings.OCR_ENABLED is False
    assert settings.OCR_PDF_SCANNED is False
    assert settings.OCR_PDF_PAGE_IMAGES is False
    assert settings.OCR_EMBEDDED_IMAGES is False


def test_validate_rejects_ocr_enabled_without_credentials(monkeypatch):
    monkeypatch.setattr(settings, "OCR_ENABLED", True)
    monkeypatch.setattr(settings, "VISION_API_KEY", "")
    monkeypatch.setattr(settings, "VISION_BASE_URL", "")

    with pytest.raises(ValueError, match="VISION_API_KEY"):
        settings._validate()

    monkeypatch.setattr(settings, "VISION_API_KEY", "test-key")
    monkeypatch.setattr(settings, "VISION_BASE_URL", "https://vision.example/v1")
    settings._validate()


# ─── 图片格式规范化 ──────────────────────────────────────────────

def test_normalize_image_bytes_converts_png_to_jpeg():
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (20, 20), (255, 0, 0)).save(buffer, format="PNG")

    normalized = normalize_image_bytes(buffer.getvalue())

    assert normalized is not None
    assert normalized[:2] == b"\xff\xd8"  # JPEG magic
    from io import BytesIO as _BytesIO
    from PIL import Image as _Image

    with _Image.open(_BytesIO(normalized)) as image:
        assert image.format == "JPEG"


def test_normalize_image_bytes_rejects_unsupported_formats():
    # EMF 文件头（Pillow 无法打开）
    emf_like = b"\x01\x00\x00\x00" + b"\x00" * 60
    assert normalize_image_bytes(emf_like) is None
    assert normalize_image_bytes(b"not an image at all") is None
