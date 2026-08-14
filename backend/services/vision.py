"""多模态视觉客户端 — 独立于问答 LLM 的 OpenAI 兼容端点。

仅用于文档入库时的 OCR 与图片理解。OCR 总开关关闭时上层不会调用本模块；
开启但未配置密钥/地址时抛出明确的 VisionError，而不是静默降级。
"""

from __future__ import annotations

import base64

from openai import OpenAI

from backend.config import settings


class VisionError(RuntimeError):
    """视觉调用失败（未配置、网络、鉴权或空返回）。"""


RECOGNIZE_PROMPT = (
    "请识别并转录图片中的全部文字，保留段落与换行，不要总结、不要翻译、"
    "不要添加任何解释。如果图片中没有文字，只回复空字符串。"
)

DESCRIBE_PROMPT = (
    "这是一份文档页面中的图片或图表。请仅描述图片/图表的内容与要点，"
    "不要重复页面正文的文字。输出适合检索的中文描述，控制在 150 字以内。"
    "如果图片没有有效信息，只回复空字符串。"
)


def normalize_image_bytes(image_bytes: bytes) -> bytes | None:
    """把任意图像字节统一转为 JPEG（RGB）字节，供视觉模型读取。

    EMF/WMF 等 Pillow 不支持的矢量格式或损坏图片返回 None，调用方应跳过。
    """
    from io import BytesIO

    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(BytesIO(image_bytes)) as image:
            converted = image.convert("RGB")
            buffer = BytesIO()
            converted.save(buffer, format="JPEG", quality=90)
            return buffer.getvalue()
    except (UnidentifiedImageError, OSError, ValueError):
        return None


def encode_image_data_url(image_bytes: bytes) -> str:
    """把（已规范化为 JPEG 的）字节编码为 data URL。"""
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _complete(image_bytes: bytes, prompt: str, max_tokens: int) -> str:
    """发起一次多模态调用并返回清洗后的文本。"""
    if not settings.VISION_API_KEY or not settings.VISION_BASE_URL:
        raise VisionError("未配置 VISION_API_KEY / VISION_BASE_URL")
    client = OpenAI(
        api_key=settings.VISION_API_KEY,
        base_url=settings.VISION_BASE_URL,
        timeout=settings.OCR_TIMEOUT_SECONDS,
        max_retries=2,
    )
    response = client.chat.completions.create(
        model=settings.VISION_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": encode_image_data_url(image_bytes)},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        temperature=0,
        max_tokens=max_tokens,
    )
    text = (response.choices[0].message.content or "").strip()
    if not text:
        raise VisionError("视觉模型返回空文本")
    return text


def recognize_text(image_bytes: bytes) -> str:
    """识别图片中的全部文字。"""
    return _complete(image_bytes, RECOGNIZE_PROMPT, max_tokens=4000)


def describe_image(image_bytes: bytes) -> str:
    """描述图片/图表内容，输出检索友好的中文说明。"""
    return _complete(image_bytes, DESCRIBE_PROMPT, max_tokens=800)
