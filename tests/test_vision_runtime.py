"""视觉运行时契约、缓存失效和诊断回归，不访问外部服务。"""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from openai import RateLimitError
from pydantic import SecretStr

from backend.config import settings
from backend.models.document_structure import SourceLocator
from backend.models.settings_schemas import VisionSettings
from backend.services import vision
from backend.services.parsers import ocr, pdf
from backend.services.vision_cache import VisionBudget


def config(**changes):
    options = VisionSettings(enabled=True, pdf_scanned_enabled=True, **changes)
    return vision.VisionCallConfig(options, "deepseek", "https://vision.example/v1", "vision-model", SecretStr("key-one"), 7)


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch, temp_dir):
    monkeypatch.setattr(settings, "VISION_CACHE_DIR", temp_dir)
    ocr.clear_failure_memo()
    yield
    ocr.clear_failure_memo()


def run(cfg, callback, warnings=None, image=b"image", task="recognize"):
    return ocr.run_vision("file", "page:1", image, callback, config=cfg, task=task,
                          budget=VisionBudget(20), warnings=warnings if warnings is not None else [],
                          locator=SourceLocator(kind="page", unit_index=0, location_label="第1页", page_number=1),
                          code="ocr_page_failed")


def test_success_cache_includes_output_settings_and_image_but_not_secret():
    cfg = config()
    callback = Mock(return_value="recognized")
    assert run(cfg, callback) == "recognized"
    assert run(replace(cfg, api_key=SecretStr("key-two")), callback) == "recognized"
    assert callback.call_count == 1
    run(config(recognize_max_tokens=6000), callback)
    run(cfg, callback, image=b"new-image")
    run(cfg, callback, task="describe")
    assert callback.call_count == 4
    disabled = replace(cfg, options=cfg.options.model_copy(update={"enabled": False}))
    assert run(disabled, callback) is None
    assert callback.call_count == 4


def test_failed_call_retries_after_key_or_timeout_change():
    cfg = config()
    callback = Mock(side_effect=vision.VisionError("视觉请求超时", code="timeout"))
    warnings = []
    run(cfg, callback, warnings)
    run(cfg, callback, warnings)
    assert callback.call_count == 1
    assert "暂未重复" in warnings[-1].message
    run(replace(cfg, api_key=SecretStr("key-two")), callback)
    run(config(recognize_timeout_seconds=120), callback)
    assert callback.call_count == 3


def test_client_uses_task_parameters_and_closes(monkeypatch):
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(choices=[
        SimpleNamespace(message=SimpleNamespace(content="result"), finish_reason="stop")
    ])
    factory = Mock(return_value=client)
    monkeypatch.setattr(vision, "OpenAI", factory)
    cfg = config(describe_thinking_mode="enabled", describe_reasoning_effort="medium",
                 describe_max_tokens=1234, describe_temperature=0.4, describe_timeout_seconds=73)
    assert vision.describe_image(b"image", config=cfg) == "result"
    factory.assert_called_once_with(api_key="key-one", base_url=cfg.base_url, timeout=73, max_retries=2)
    call = client.chat.completions.create.call_args.kwargs
    assert call["model"] == cfg.model
    assert call["max_tokens"] == 1234 and call["temperature"] == 0.4
    assert call["extra_body"] == {"thinking": {"type": "enabled"}}
    assert call["reasoning_effort"] == "high"
    client.close.assert_called_once()


@pytest.mark.parametrize("reason,content,code", [
    ("length", "partial", "truncated"), ("stop", "", "empty_response"),
    ("content_filter", "", "content_filtered"),
])
def test_invalid_completions_are_not_cached(monkeypatch, reason, content, code):
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(choices=[
        SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=reason)
    ])
    monkeypatch.setattr(vision, "OpenAI", Mock(return_value=client))
    with pytest.raises(vision.VisionError) as error:
        vision.recognize_text(b"image", config=config())
    assert error.value.code == code
    assert run(config(), vision.recognize_text) is None
    ocr.clear_failure_memo()
    assert run(config(), Mock(return_value="recovered")) == "recovered"


def test_rate_limit_exposes_safe_code_and_request_id_not_raw_body(monkeypatch):
    response = httpx.Response(429, request=httpx.Request("POST", "https://vision.example"),
                              headers={"x-request-id": "request-123"})
    client = Mock()
    client.chat.completions.create.side_effect = RateLimitError(
        "SECRET-and-private-image", response=response,
        body={"error": {"code": "rate_limit_exceeded", "message": "SECRET-and-private-image"}},
    )
    monkeypatch.setattr(vision, "OpenAI", Mock(return_value=client))
    with pytest.raises(vision.VisionError) as raised:
        vision.recognize_text(b"image", config=config())
    error = raised.value
    assert error.code == "rate_limited" and error.status_code == 429
    assert error.request_id == "request-123"
    assert "rate_limit_exceeded" in str(error)
    assert "SECRET" not in str(error)


def test_probe_requires_actual_image_reading_and_does_not_leak_answer_in_prompt(monkeypatch):
    monkeypatch.setattr(vision, "uuid4", lambda: SimpleNamespace(hex="abcd1234"))
    complete = Mock(return_value="RAG ABCD1234")
    monkeypatch.setattr(vision, "_complete", complete)
    assert vision.test_vision_connection(config()).success
    args = complete.call_args
    assert args.args[0].startswith(b"\xff\xd8")
    assert "ABCD1234" not in args.args[1]
    assert args.kwargs["retries"] == 0
    complete.return_value = "OK"
    result = vision.test_vision_connection(config(), task="describe")
    assert not result.success and result.error_code == "image_verification_failed"


def test_pdf_snapshot_overrides_environment_and_budget(monkeypatch):
    cfg = config(max_calls_per_document=1, max_image_edge=1024)
    monkeypatch.setattr(pdf, "read_page_texts_pypdf2", lambda _: ["", ""])
    monkeypatch.setattr(pdf, "file_sha256", lambda _: "pdf-hash")
    render = Mock(return_value=b"image")
    recognize = Mock(return_value="识别到的正文")
    monkeypatch.setattr(pdf, "render_page_image", render)
    monkeypatch.setattr(pdf, "recognize_text", recognize)
    parsed = pdf.parse_pdf_document("unused.pdf", document_id="doc", vision_config=cfg)
    assert recognize.call_count == 1
    assert recognize.call_args.kwargs["config"] is cfg
    assert render.call_args.args[-1] == 1024
    assert parsed.units[0].blocks[0].attributes["vision_settings_revision"] == 7
    assert "视觉识别未完成" in parsed.units[1].blocks[0].text
    assert "上限" in parsed.warnings[-1].message
