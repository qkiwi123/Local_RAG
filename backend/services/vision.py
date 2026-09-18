"""文档视觉客户端：一次解析绑定配置，连接可跟随问答或独立指定。"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
from io import BytesIO
import json
import re
from threading import BoundedSemaphore
from time import perf_counter
from uuid import uuid4

from openai import OpenAI, APIConnectionError, APITimeoutError
from pydantic import SecretStr
from backend.config import settings
from backend.models.settings_schemas import (
    EffectiveSettingsSnapshot, VisionSettings, VisionConnectionTestResponse,
)

RECOGNIZE_PROMPT = (
    "请识别并转录图片中的全部文字，保留段落与换行，不要总结、不要翻译、"
    "不要添加任何解释。如果图片中没有文字，只回复空字符串。"
)
DESCRIBE_PROMPT = (
    "这是一份文档页面中的图片或图表。请仅描述图片/图表的内容与要点，"
    "不要重复页面正文的文字。输出适合检索的中文描述，控制在 150 字以内。"
    "如果图片没有有效信息，只回复空字符串。"
)
_CALL_SLOT = BoundedSemaphore(1)


class VisionError(RuntimeError):
    """安全诊断，不保留包含密钥/图片的上游原始消息。"""
    def __init__(self, message: str, *, code: str = "upstream_error",
                 status_code: int | None = None, request_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.request_id = request_id


@dataclass(frozen=True)
class VisionCallConfig:
    options: VisionSettings
    provider: str
    base_url: str
    model: str
    api_key: SecretStr | None = field(repr=False)
    revision: int = -1

    def fingerprint(self, task: str) -> str:
        values = {
            "contract": "vision-v2", "provider": self.provider,
            "base_url": self.base_url.rstrip("/"), "model": self.model,
            "task": task, "max_image_edge": self.options.max_image_edge,
            "prompt": RECOGNIZE_PROMPT if task == "recognize" else DESCRIBE_PROMPT,
            **{name: value for name, value in self.options.model_dump().items()
               if name.startswith(task + "_") and not name.endswith("timeout_seconds")},
        }
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()

    def credential_fingerprint(self) -> str:
        # 只用于内存失败去重，不落盘、不展示。
        return hashlib.sha256((self.api_key.get_secret_value() if self.api_key else "").encode()).hexdigest()


def vision_config_from_snapshot(snapshot: EffectiveSettingsSnapshot) -> VisionCallConfig:
    following = snapshot.vision.source == "follow_llm"
    connection = snapshot.llm if following else snapshot.vision
    return VisionCallConfig(
        options=snapshot.vision, provider=connection.provider,
        base_url=connection.base_url, model=connection.model,
        api_key=snapshot.api_key if following else snapshot.vision_api_key,
        revision=snapshot.revision,
    )


def legacy_vision_config() -> VisionCallConfig:
    """直接调用旧解析函数的兼容默认；上传和重解析入口显式传快照。"""
    options = VisionSettings(
        source="custom", enabled=settings.OCR_ENABLED,
        provider=getattr(settings, "VISION_PROVIDER", "openai_compatible"),
        base_url=settings.VISION_BASE_URL, model=settings.VISION_MODEL,
        pdf_scanned_enabled=settings.OCR_PDF_SCANNED,
        pdf_page_images_enabled=settings.OCR_PDF_PAGE_IMAGES,
        embedded_images_enabled=settings.OCR_EMBEDDED_IMAGES,
        max_image_edge=settings.OCR_MAX_IMAGE_EDGE,
        max_calls_per_document=settings.OCR_MAX_PER_DOCUMENT,
        recognize_timeout_seconds=settings.OCR_TIMEOUT_SECONDS,
        describe_timeout_seconds=settings.OCR_TIMEOUT_SECONDS,
        recognize_thinking_mode="provider_default", describe_thinking_mode="provider_default",
    )
    return VisionCallConfig(options, options.provider, options.base_url, options.model,
                            SecretStr(settings.VISION_API_KEY) if settings.VISION_API_KEY else None)


def normalize_image_bytes(image_bytes: bytes, max_edge: int | None = None) -> bytes | None:
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            converted = image.convert("RGB")
            if max_edge is not None:
                converted.thumbnail((max_edge, max_edge))
            buffer = BytesIO()
            converted.save(buffer, format="JPEG", quality=90)
            return buffer.getvalue()
    except (UnidentifiedImageError, OSError, ValueError):
        return None


def encode_image_data_url(image_bytes: bytes) -> str:
    return f"data:image/jpeg;base64,{base64.b64encode(image_bytes).decode('ascii')}"


def _generation_parameters(config: VisionCallConfig, task: str) -> dict:
    if task not in {"recognize", "describe"}:
        raise ValueError("未知视觉任务")
    values = config.options
    mode = getattr(values, f"{task}_thinking_mode")
    effort = getattr(values, f"{task}_reasoning_effort")
    result = {"temperature": getattr(values, f"{task}_temperature"),
              "max_tokens": getattr(values, f"{task}_max_tokens")}
    if config.provider == "deepseek":
        if mode != "provider_default":
            result["extra_body"] = {"thinking": {"type": mode}}
        if mode != "disabled" and effort != "provider_default":
            result["reasoning_effort"] = "high" if effort == "medium" else effort
    elif mode == "disabled":
        result["reasoning_effort"] = "none"
    elif effort != "provider_default":
        result["reasoning_effort"] = effort
    elif mode == "enabled":
        result["reasoning_effort"] = "medium"
    return result


def _safe_identifier(value) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_.:/-]{1,160}", value) else None


def _upstream_error(error: Exception) -> VisionError:
    status = getattr(error, "status_code", None)
    request_id = _safe_identifier(getattr(error, "request_id", None))
    if isinstance(error, APITimeoutError):
        code, message = "timeout", "视觉请求超时"
    elif isinstance(error, APIConnectionError):
        code, message = "network_error", "无法连接视觉接口"
    else:
        code, message = {
            401: ("authentication_failed", "视觉接口鉴权失败，请检查 API Key"),
            403: ("permission_denied", "视觉接口拒绝访问，请检查模型权限"),
            404: ("model_not_found", "视觉模型或接口地址不存在"),
            429: ("rate_limited", "视觉接口限流（HTTP 429）"),
            400: ("invalid_request", "视觉接口拒绝请求，请检查图片支持及生成参数"),
            422: ("invalid_request", "视觉接口不接受当前图片或生成参数"),
        }.get(status, ("upstream_error", "视觉接口调用失败"))
    details = []
    if isinstance(status, int):
        details.append(f"HTTP {status}")
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        info = body.get("error", body)
        upstream_code = _safe_identifier(str(info.get("code", ""))) if isinstance(info, dict) else None
        if upstream_code:
            details.append(f"服务商代码 {upstream_code}")
    if request_id:
        details.append(f"请求 ID {request_id}")
    return VisionError(message + ("；" + "；".join(details) if details else ""),
                       code=code, status_code=status, request_id=request_id)


def _complete(image_bytes: bytes, prompt: str, max_tokens: int | None = None,
              *, config: VisionCallConfig | None = None, task: str = "recognize",
              retries: int = 2) -> str:
    resolved = config or legacy_vision_config()
    if config is not None and not resolved.options.enabled:
        raise VisionError("视觉识别已关闭", code="disabled")
    if not resolved.api_key or not resolved.base_url:
        raise VisionError("未配置视觉 API Key / VISION_BASE_URL", code="not_configured")
    timeout = getattr(resolved.options, f"{task}_timeout_seconds")
    params = _generation_parameters(resolved, task)
    if max_tokens is not None:
        params["max_tokens"] = max_tokens
    if not _CALL_SLOT.acquire(timeout=timeout):
        raise VisionError("等待视觉调用超时", code="timeout")
    client = None
    try:
        client = OpenAI(api_key=resolved.api_key.get_secret_value(), base_url=resolved.base_url,
                        timeout=timeout, max_retries=retries)
        response = client.chat.completions.create(
            model=resolved.model,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": encode_image_data_url(image_bytes)}},
                {"type": "text", "text": prompt},
            ]}], **params,
        )
        request_id = _safe_identifier(getattr(response, "_request_id", None))
        if not response.choices:
            raise VisionError("视觉模型未返回候选内容", code="empty_response", request_id=request_id)
        choice = response.choices[0]
        finish_reason = getattr(choice, "finish_reason", None)
        if finish_reason == "length":
            raise VisionError("视觉输出达到 Tokens 上限，结果可能截断；请提高输出上限后重新解析", code="truncated", request_id=request_id)
        if finish_reason == "content_filter":
            raise VisionError("视觉输出被服务商过滤", code="content_filtered", request_id=request_id)
        text = (choice.message.content or "").strip()
        if not text:
            raise VisionError("视觉模型返回空文本，无法确认页面是否有文字", code="empty_response", request_id=request_id)
        return text
    except VisionError:
        raise
    except Exception as error:
        raise _upstream_error(error) from None
    finally:
        _CALL_SLOT.release()
        if client is not None and callable(getattr(client, "close", None)):
            try:
                client.close()
            except Exception:
                pass  # 清理失败不能覆盖真正的调用结果或诊断。


def recognize_text(image_bytes: bytes, *, config: VisionCallConfig | None = None) -> str:
    return _complete(image_bytes, RECOGNIZE_PROMPT, config=config, task="recognize")


def describe_image(image_bytes: bytes, *, config: VisionCallConfig | None = None) -> str:
    return _complete(image_bytes, DESCRIBE_PROMPT, config=config, task="describe")


def test_vision_connection(config: VisionCallConfig, *, task: str = "recognize") -> VisionConnectionTestResponse:
    from PIL import Image, ImageDraw, ImageFont
    token = "RAG " + uuid4().hex[:8].upper()
    image = Image.new("RGB", (900, 220), "white")
    font = ImageFont.load_default(size=56)
    ImageDraw.Draw(image).text((40, 70), token, font=font, fill="black")
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    started = perf_counter()
    try:
        payload = normalize_image_bytes(buffer.getvalue(), config.options.max_image_edge)
        result = _complete(payload, "请读取图片，逐字输出图片中的识别码。", config=config, task=task, retries=0)
        if re.sub(r"\W", "", token).lower() not in re.sub(r"\W", "", result).lower():
            raise VisionError("接口有响应，但未正确读出测试图片；请确认模型支持图片输入", code="image_verification_failed")
        error = None
    except VisionError as exc:
        error = exc
    return VisionConnectionTestResponse(
        success=error is None, latency_ms=(perf_counter() - started) * 1000,
        provider=config.provider, model=config.model, endpoint=config.base_url,
        error_code=error.code if error else None, message=str(error) if error else "图片识别验证成功",
        request_id=error.request_id if error else None,
    )
