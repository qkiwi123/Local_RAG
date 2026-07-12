"""
LLM 调用与 Prompt 拼接 — 将检索到的上下文和用户问题组装成 Prompt，
调用 DeepSeek API 生成答案。

DeepSeek API 兼容 OpenAI 接口格式，因此可以直接使用 openai 库。
只需将 base_url 指向 DeepSeek 即可（https://api.deepseek.com）。

流式输出（stream=True）的原理：
- 非流式：等 LLM 生成完所有 token 后一次性返回
- 流式：LLM 每生成一个 token 就 yield 出去，前端逐字显示
"""

from typing import List, Generator
from openai import OpenAI
from backend.config import settings


# 模块级单例：整个应用共享同一个 OpenAI 客户端
_client = OpenAI(
    api_key=settings.DEEPSEEK_API_KEY,
    base_url=settings.DEEPSEEK_BASE_URL,
)


SYSTEM_PROMPT = """你是一个企业知识库问答助手。请根据以下提供的文档内容回答用户的问题。

规则：
1. 只能根据提供的文档内容回答，如果文档中没有相关信息，请明确告知用户。
2. 回答时请引用来源，格式为 【文档名，第X页】。
3. 回答应简洁准确，不要编造文档中没有的信息。
4. 如果文档内容之间存在矛盾，请指出并分别说明。"""


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


def ask(question: str, context: str) -> str:
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
    messages = build_messages(question, context)

    response = _client.chat.completions.create(
        model=settings.DEEPSEEK_MODEL,
        messages=messages,
        temperature=0.3,  # 低温度：更确定性的回答，减少幻觉
        max_tokens=2000,
    )

    return response.choices[0].message.content


def ask_stream(question: str, context: str) -> Generator[str, None, None]:
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
    messages = build_messages(question, context)

    stream = _client.chat.completions.create(
        model=settings.DEEPSEEK_MODEL,
        messages=messages,
        temperature=0.3,
        max_tokens=2000,
        stream=True,
    )

    for chunk in stream:
        if chunk.choices[0].delta.content is not None:
            yield chunk.choices[0].delta.content
