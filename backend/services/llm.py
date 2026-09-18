"""
LLM 调用与 Prompt 拼接 — 将检索到的上下文和用户问题组装成 Prompt，
调用 DeepSeek API 生成答案。

DeepSeek API 兼容 OpenAI 接口格式，因此可以直接使用 openai 库。
只需将 base_url 指向 DeepSeek 即可（https://api.deepseek.com）。

流式输出（stream=True）的原理：
- 非流式：等 LLM 生成完所有 token 后一次性返回
- 流式：LLM 每生成一个 token 就 yield 出去，前端逐字显示
"""

from collections import OrderedDict
from dataclasses import dataclass, field
from hashlib import sha256
from threading import RLock
from time import perf_counter
from typing import AsyncGenerator, Generator, List
from urllib.parse import urlsplit

from openai import AsyncOpenAI, OpenAI
from pydantic import SecretStr

from backend.config import settings
from backend.models.settings_schemas import (
    EffectiveSettingsSnapshot,
    LLMConnectionTestResponse,
)


@dataclass(frozen=True)
class LLMCallConfig:
    revision: int
    enabled: bool
    provider: str
    base_url: str
    model: str
    api_key: SecretStr | None = field(repr=False)
    temperature: float
    max_tokens: int
    timeout_seconds: float
    answer_thinking_mode: str
    answer_reasoning_effort: str
    rewrite_enabled: bool
    rewrite_thinking_mode: str
    rewrite_reasoning_effort: str
    rewrite_temperature: float
    rewrite_max_tokens: int
    rewrite_timeout_seconds: float


@dataclass(frozen=True)
class ReasoningDelta:
    """上游实际返回的思考片段，与最终正文严格分离。"""

    text: str


class LLMDisabledError(RuntimeError):
    """调用方在生成模型关闭时错误发起了模型调用。"""


class EmptyLLMResponseError(RuntimeError):
    """上游调用成功结束，但没有返回可展示的最终正文。"""

    def __init__(self, *, reasoning_only: bool = False):
        self.reasoning_only = reasoning_only
        if reasoning_only:
            message = (
                "模型只返回了思考内容，没有生成最终回答。"
                "请关闭回答思考模式，或提高最大输出 Tokens 后重试。"
            )
        else:
            message = "模型未返回可见正文，请检查模型配置或稍后重试。"
        super().__init__(message)


_FOLLOWUP_REWRITE_SYSTEM_PROMPT = """你是检索查询改写器。仅根据对话历史，把当前追问改写为一个语义完整、可独立检索的问题。
严格遵守以下规则：
1. 只补齐当前追问中省略的对象、条件或指代，优先使用最近且明确的上下文。
2. 保留当前追问的意图、对象、条件、时间范围、选项和比较范围；不得把单一对象扩大成多个对象或新增比较。
3. 不得回答问题，不得补充对话历史中不存在的事实。当前追问已经完整时，原样输出。
4. 只输出一行改写后的问题，不要解释、前缀、引号、列表或其他内容。"""


_CLIENT_CACHE_LIMIT = 4
_CLIENT_CACHE: OrderedDict[tuple, OpenAI] = OrderedDict()
_ASYNC_CLIENT_CACHE: OrderedDict[tuple, AsyncOpenAI] = OrderedDict()
_CLIENT_CACHE_LOCK = RLock()


def call_config_from_snapshot(
    runtime: EffectiveSettingsSnapshot | None = None,
    *,
    overrides: dict | None = None,
    api_key: SecretStr | None = None,
    use_snapshot_key: bool = True,
) -> LLMCallConfig:
    """把一次请求的冻结快照转换为 LLM 调用配置。"""
    if runtime is None:
        values = {
            "enabled": bool(getattr(settings, "LLM_ENABLED", True)),
            "provider": str(getattr(settings, "LLM_PROVIDER", "deepseek")),
            "base_url": settings.DEEPSEEK_BASE_URL,
            "model": settings.DEEPSEEK_MODEL,
            "temperature": float(getattr(settings, "LLM_TEMPERATURE", 0.3)),
            "max_tokens": int(getattr(settings, "LLM_MAX_TOKENS", 2000)),
            "timeout_seconds": float(
                getattr(settings, "LLM_TIMEOUT_SECONDS", 120)
            ),
            "answer_thinking_mode": str(
                getattr(settings, "LLM_ANSWER_THINKING_MODE", "disabled")
            ),
            "answer_reasoning_effort": str(
                getattr(settings, "LLM_ANSWER_REASONING_EFFORT", "provider_default")
            ),
            "rewrite_enabled": bool(
                getattr(settings, "LLM_REWRITE_ENABLED", True)
            ),
            "rewrite_thinking_mode": str(
                getattr(settings, "LLM_REWRITE_THINKING_MODE", "disabled")
            ),
            "rewrite_reasoning_effort": str(
                getattr(settings, "LLM_REWRITE_REASONING_EFFORT", "provider_default")
            ),
            "rewrite_temperature": float(
                getattr(settings, "LLM_REWRITE_TEMPERATURE", 0)
            ),
            "rewrite_max_tokens": int(
                getattr(settings, "LLM_REWRITE_MAX_TOKENS", 200)
            ),
            "rewrite_timeout_seconds": float(
                getattr(settings, "LLM_REWRITE_TIMEOUT_SECONDS", 30)
            ),
        }
        resolved_key = SecretStr(settings.DEEPSEEK_API_KEY)
        revision = -1
    else:
        values = runtime.llm.model_dump()
        resolved_key = runtime.api_key if use_snapshot_key else api_key
        revision = runtime.revision
    values.update(overrides or {})
    if runtime is None and not use_snapshot_key:
        resolved_key = api_key
    return LLMCallConfig(
        revision=revision,
        api_key=resolved_key,
        **values,
    )


def _client_key(config: LLMCallConfig) -> tuple:
    secret = (
        config.api_key.get_secret_value()
        if config.api_key is not None
        else ""
    )
    key_fingerprint = sha256(secret.encode("utf-8")).hexdigest()
    return (
        config.revision,
        config.provider,
        config.base_url,
        config.timeout_seconds,
        key_fingerprint,
    )


def _create_client(config: LLMCallConfig) -> OpenAI:
    if config.api_key is None:
        raise ValueError("未配置 LLM API Key")
    secret = config.api_key.get_secret_value().strip()
    if not secret:
        raise ValueError("未配置 LLM API Key")
    return OpenAI(
        api_key=secret,
        base_url=config.base_url,
        timeout=config.timeout_seconds,
    )


def _create_async_client(config: LLMCallConfig) -> AsyncOpenAI:
    """创建原生异步客户端，避免流式迭代阻塞 ASGI 事件循环。"""
    if config.api_key is None:
        raise ValueError("未配置 LLM API Key")
    secret = config.api_key.get_secret_value().strip()
    if not secret:
        raise ValueError("未配置 LLM API Key")
    return AsyncOpenAI(
        api_key=secret,
        base_url=config.base_url,
        timeout=config.timeout_seconds,
    )


def _get_client(config: LLMCallConfig | None = None) -> OpenAI:
    """按 revision 和连接参数惰性复用客户端，不把明文 Key 作为键。"""
    resolved = config or call_config_from_snapshot()
    key = _client_key(resolved)
    with _CLIENT_CACHE_LOCK:
        cached = _CLIENT_CACHE.get(key)
        if cached is not None:
            _CLIENT_CACHE.move_to_end(key)
            return cached
        client = _create_client(resolved)
        _CLIENT_CACHE[key] = client
        while len(_CLIENT_CACHE) > _CLIENT_CACHE_LIMIT:
            _CLIENT_CACHE.popitem(last=False)
        return client


def _get_async_client(
    config: LLMCallConfig | None = None,
) -> AsyncOpenAI:
    """按同步客户端相同的安全键策略复用异步客户端。"""
    resolved = config or call_config_from_snapshot()
    key = _client_key(resolved)
    with _CLIENT_CACHE_LOCK:
        cached = _ASYNC_CLIENT_CACHE.get(key)
        if cached is not None:
            _ASYNC_CLIENT_CACHE.move_to_end(key)
            return cached
        client = _create_async_client(resolved)
        _ASYNC_CLIENT_CACHE[key] = client
        while len(_ASYNC_CLIENT_CACHE) > _CLIENT_CACHE_LIMIT:
            _ASYNC_CLIENT_CACHE.popitem(last=False)
        return client


def clear_llm_client_cache() -> None:
    """保存 LLM 设置后释放缓存引用；已持有旧客户端的请求可继续完成。"""
    with _CLIENT_CACHE_LOCK:
        _CLIENT_CACHE.clear()
        _ASYNC_CLIENT_CACHE.clear()


def _require_enabled(config: LLMCallConfig) -> None:
    if not config.enabled:
        raise LLMDisabledError("生成模型已关闭")


def _visible_message_content(message) -> str:
    """提取最终正文；禁止把 reasoning-only 响应当作成功答案。"""
    content = getattr(message, "content", None)
    if content is not None and str(content).strip():
        return str(content)
    raise EmptyLLMResponseError(
        reasoning_only=bool(getattr(message, "reasoning_content", None))
    )


def _thinking_parameters(
    config: LLMCallConfig,
    *,
    mode: str,
    effort: str,
) -> dict:
    """把通用思考意图映射到当前 provider 的 Chat Completions 参数。

    provider_default 不发送任何思考参数。DeepSeek 使用其 thinking 扩展；
    通用 OpenAI-compatible 端点使用 reasoning_effort。兼容端点是否支持
    对应参数由上游决定，因此设置页只提示、不禁止用户组合参数。
    """
    params: dict = {}
    if config.provider == "deepseek":
        if mode != "provider_default":
            params["extra_body"] = {"thinking": {"type": mode}}
        # DeepSeek 当前支持 low/high/max；medium 按官方规则映射到 high。
        if mode != "disabled" and effort != "provider_default":
            params["reasoning_effort"] = "high" if effort == "medium" else effort
        return params

    if mode == "disabled":
        params["reasoning_effort"] = "none"
    elif effort != "provider_default":
        params["reasoning_effort"] = effort
    elif mode == "enabled":
        params["reasoning_effort"] = "medium"
    return params


def _generation_parameters(config: LLMCallConfig, purpose: str) -> dict:
    """返回回答或查询改写实际提交的通用生成参数。"""
    if purpose == "answer":
        params = {
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
        }
        params.update(
            _thinking_parameters(
                config,
                mode=config.answer_thinking_mode,
                effort=config.answer_reasoning_effort,
            )
        )
        return params
    if purpose == "rewrite":
        params = {
            "temperature": config.rewrite_temperature,
            "max_tokens": config.rewrite_max_tokens,
            "timeout": config.rewrite_timeout_seconds,
        }
        params.update(
            _thinking_parameters(
                config,
                mode=config.rewrite_thinking_mode,
                effort=config.rewrite_reasoning_effort,
            )
        )
        return params
    raise ValueError(f"未知 LLM 调用用途: {purpose}")


def build_retrieval_only_answer(source_count: int) -> str:
    """生成不调用模型、但能被现有引用解析器识别的固定回答。"""
    markers = " ".join(f"【来源{index}】" for index in range(1, source_count + 1))
    return (
        "生成模型已关闭，已返回本次检索到的相关文档来源。"
        "请通过引用查看原文：" + markers
    )


def _endpoint_origin(base_url: str) -> str:
    parsed = urlsplit(base_url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _connection_error(error: Exception) -> tuple[str, str]:
    status_code = getattr(error, "status_code", None)
    class_name = type(error).__name__.lower()
    if status_code == 401 or "authentication" in class_name:
        return "authentication_failed", "API Key 无效或认证失败"
    if status_code == 403 or "permission" in class_name:
        return "permission_denied", "API Key 没有访问该模型的权限"
    if status_code == 404 or "notfound" in class_name:
        return "model_not_found", "模型或接口地址不存在"
    if isinstance(error, TimeoutError) or "timeout" in class_name:
        return "timeout", "连接模型服务超时"
    if isinstance(error, ConnectionError) or "connection" in class_name:
        return "network_error", "无法连接模型服务"
    return "upstream_error", "模型服务返回了未分类错误"


def test_llm_connection(config: LLMCallConfig) -> LLMConnectionTestResponse:
    """对草稿配置执行一次最小请求；不使用或污染正式客户端缓存。"""
    started = perf_counter()
    endpoint = _endpoint_origin(config.base_url)
    if config.api_key is None:
        return LLMConnectionTestResponse(
            success=False,
            latency_ms=0,
            provider=config.provider,
            model=config.model,
            endpoint=endpoint,
            error_code="authentication_failed",
            message="尚未配置 API Key",
        )
    try:
        client = _create_client(config)
        client.chat.completions.create(
            model=config.model,
            messages=[{"role": "user", "content": "ping"}],
            temperature=0,
            max_tokens=1,
        )
    except Exception as error:
        code, message = _connection_error(error)
        return LLMConnectionTestResponse(
            success=False,
            latency_ms=(perf_counter() - started) * 1000,
            provider=config.provider,
            model=config.model,
            endpoint=endpoint,
            error_code=code,
            message=message,
        )
    return LLMConnectionTestResponse(
        success=True,
        latency_ms=(perf_counter() - started) * 1000,
        provider=config.provider,
        model=config.model,
        endpoint=endpoint,
        message="连接成功",
    )


SYSTEM_PROMPT = """你是一个企业知识库问答助手。请根据以下提供的文档内容回答用户的问题。

规则：
1. 只能根据提供的文档内容回答，如果文档中没有相关信息，请明确告知用户。
2. 文档片段使用【来源N】编号。回答中的每个关键结论都应引用对应来源，格式只能是【来源N】。
3. N 必须是提供的文档片段中真实存在的编号；不要在方括号内自行填写文档名或页码。
4. 如果文档不足以回答，明确回答“根据现有文档，未找到相关信息”，且不要附加来源编号。
5. 回答应简洁准确，不要编造文档中没有的信息。
6. 如果文档内容之间存在矛盾，请指出并分别引用各自来源。"""


def build_messages(question: str, context: str) -> List[dict]:
    """
    构建发送给 LLM 的 messages 列表。

    格式遵循 OpenAI 兼容 API：
    - system: 系统提示词（角色设定 + 行为规则）
    - user: 文档上下文 + 用户问题

    Args:
        question: 用户的自然语言问题
        context: 从向量库检索并格式化后的文档上下文

    Returns:
        messages 列表，可直接传入 OpenAI client
    """
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"文档内容：\n{context}\n\n用户问题：{question}"
        },
    ]


def _build_history_messages(
    question: str,
    standalone_question: str,
    context: str,
    history: str,
) -> List[dict]:
    """构造带历史的统一消息，确保同步与异步入口使用相同提示词。"""
    return [
        {
            "role": "system",
            "content": SYSTEM_PROMPT + (
                "\n7. 对话历史只用于理解指代和保持表达连贯，不能作为事实证据；"
                "事实仍必须由本轮文档内容支持。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"对话历史：\n{history or '（无）'}\n\n"
                f"本轮检索问题：{standalone_question}\n\n"
                f"本轮文档内容：\n{context}\n\n"
                f"用户当前原始问题：{question}"
            ),
        },
    ]


def ask(
    question: str,
    context: str,
    runtime: EffectiveSettingsSnapshot | None = None,
) -> str:
    """
    向 LLM 提问，获取完整的回答文本。

    使用非流式模式，等待 LLM 生成完所有内容后一次性返回。
    适合 API 接口场景。

    Args:
        question: 用户问题
        context: 格式化后的检索上下文

    Returns:
        LLM 生成的回答文本
    """
    config = call_config_from_snapshot(runtime)
    _require_enabled(config)
    messages = build_messages(question, context)

    response = _get_client(config).chat.completions.create(
        model=config.model,
        messages=messages,
        **_generation_parameters(config, "answer"),
    )

    return _visible_message_content(response.choices[0].message)


def rewrite_followup(
    question: str,
    history: str,
    runtime: EffectiveSettingsSnapshot | None = None,
) -> str:
    """把含指代或省略的追问改写为可独立检索的问题。"""
    config = call_config_from_snapshot(runtime)
    _require_enabled(config)
    request_kwargs = {
        "model": config.model,
        "messages": [
            {
                "role": "system",
                "content": _FOLLOWUP_REWRITE_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": f"对话历史：\n{history}\n\n当前追问：{question}",
            },
        ],
        **_generation_parameters(config, "rewrite"),
    }

    response = _get_client(config).chat.completions.create(
        **request_kwargs,
    )
    rewritten = (response.choices[0].message.content or "").strip()
    if not rewritten:
        raise ValueError("追问改写返回空文本")
    return rewritten


def ask_with_history(
    question: str,
    standalone_question: str,
    context: str,
    history: str,
    runtime: EffectiveSettingsSnapshot | None = None,
) -> str:
    """使用有界历史保持对话连贯，但事实仍只取自当前检索证据。"""
    config = call_config_from_snapshot(runtime)
    _require_enabled(config)
    response = _get_client(config).chat.completions.create(
        model=config.model,
        messages=_build_history_messages(
            question,
            standalone_question,
            context,
            history,
        ),
        **_generation_parameters(config, "answer"),
    )
    return _visible_message_content(response.choices[0].message)


def ask_with_history_stream(
    question: str,
    standalone_question: str,
    context: str,
    history: str,
    runtime: EffectiveSettingsSnapshot | None = None,
) -> Generator[str, None, None]:
    """流式版 ask_with_history：逐 token 产出，历史只用于连贯不作文档证据。"""
    config = call_config_from_snapshot(runtime)
    _require_enabled(config)
    stream = _get_client(config).chat.completions.create(
        model=config.model,
        messages=_build_history_messages(
            question,
            standalone_question,
            context,
            history,
        ),
        **_generation_parameters(config, "answer"),
        stream=True,
    )

    saw_visible_content = False
    saw_reasoning_content = False
    for chunk in stream:
        delta = chunk.choices[0].delta
        saw_reasoning_content = saw_reasoning_content or bool(
            getattr(delta, "reasoning_content", None)
        )
        content = getattr(delta, "content", None)
        if content:
            saw_visible_content = saw_visible_content or bool(content.strip())
            yield content
    if not saw_visible_content:
        raise EmptyLLMResponseError(reasoning_only=saw_reasoning_content)


async def ask_with_history_stream_async(
    question: str,
    standalone_question: str,
    context: str,
    history: str,
    runtime: EffectiveSettingsSnapshot | None = None,
    *,
    include_reasoning: bool = False,
) -> AsyncGenerator[str | ReasoningDelta, None]:
    """原生异步流式问答；每次上游读取都会把控制权交还事件循环。"""
    config = call_config_from_snapshot(runtime)
    _require_enabled(config)
    stream = await _get_async_client(config).chat.completions.create(
        model=config.model,
        messages=_build_history_messages(
            question,
            standalone_question,
            context,
            history,
        ),
        **_generation_parameters(config, "answer"),
        stream=True,
    )

    saw_visible_content = False
    saw_reasoning_content = False
    try:
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None)
            saw_reasoning_content = saw_reasoning_content or bool(reasoning)
            if include_reasoning and reasoning:
                yield ReasoningDelta(text=reasoning)
            content = getattr(delta, "content", None)
            if content:
                saw_visible_content = saw_visible_content or bool(content.strip())
                yield content
    finally:
        await stream.close()
    if not saw_visible_content:
        raise EmptyLLMResponseError(reasoning_only=saw_reasoning_content)


def ask_stream(
    question: str,
    context: str,
    runtime: EffectiveSettingsSnapshot | None = None,
) -> Generator[str, None, None]:
    """
    流式向 LLM 提问，逐 token 返回生成的文本。

    使用 stream=True，LLM 每生成一个 token 就 yield 出去。
    前端配合 st.write_stream() 可实现逐字显示（类似 ChatGPT 效果）。

    Args:
        question: 用户问题
        context: 格式化后的检索上下文

    Yields:
        每次 yield 一个文本片段（可能是单个字符到一个 token 不等）
    """
    config = call_config_from_snapshot(runtime)
    _require_enabled(config)
    messages = build_messages(question, context)

    stream = _get_client(config).chat.completions.create(
        model=config.model,
        messages=messages,
        **_generation_parameters(config, "answer"),
        stream=True,
    )

    saw_visible_content = False
    saw_reasoning_content = False
    for chunk in stream:
        delta = chunk.choices[0].delta
        saw_reasoning_content = saw_reasoning_content or bool(
            getattr(delta, "reasoning_content", None)
        )
        content = getattr(delta, "content", None)
        if content:
            saw_visible_content = saw_visible_content or bool(content.strip())
            yield content
    if not saw_visible_content:
        raise EmptyLLMResponseError(reasoning_only=saw_reasoning_content)


async def ask_stream_async(
    question: str,
    context: str,
    runtime: EffectiveSettingsSnapshot | None = None,
    *,
    include_reasoning: bool = False,
) -> AsyncGenerator[str | ReasoningDelta, None]:
    """原生异步流式问答，供 ASGI 流式端点逐块发送。"""
    config = call_config_from_snapshot(runtime)
    _require_enabled(config)
    messages = build_messages(question, context)

    stream = await _get_async_client(config).chat.completions.create(
        model=config.model,
        messages=messages,
        **_generation_parameters(config, "answer"),
        stream=True,
    )

    saw_visible_content = False
    saw_reasoning_content = False
    try:
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None)
            saw_reasoning_content = saw_reasoning_content or bool(reasoning)
            if include_reasoning and reasoning:
                yield ReasoningDelta(text=reasoning)
            content = getattr(delta, "content", None)
            if content:
                saw_visible_content = saw_visible_content or bool(content.strip())
                yield content
    finally:
        await stream.close()
    if not saw_visible_content:
        raise EmptyLLMResponseError(reasoning_only=saw_reasoning_content)
