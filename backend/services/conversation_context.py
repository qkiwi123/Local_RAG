"""确定性裁剪多轮历史，避免完整会话无限进入模型。"""

from backend.config import settings
from backend.services.embedding import count_tokens


def bounded_history(messages: list[dict]) -> list[dict]:
    candidates = messages[-settings.CONVERSATION_HISTORY_MESSAGES:]
    selected: list[dict] = []
    token_total = 0
    for message in reversed(candidates):
        content = message["content"][:settings.CONVERSATION_MESSAGE_MAX_CHARS]
        tokens = count_tokens(content)
        if selected and token_total + tokens > settings.CONVERSATION_HISTORY_MAX_TOKENS:
            break
        if tokens > settings.CONVERSATION_HISTORY_MAX_TOKENS:
            content = content[: max(1, len(content) // 2)]
            tokens = count_tokens(content)
        selected.append({**message, "content": content})
        token_total += tokens
    return list(reversed(selected))


def format_history(messages: list[dict]) -> str:
    labels = {"user": "用户", "assistant": "助手"}
    return "\n".join(
        f"{labels.get(message['role'], message['role'])}：{message['content']}"
        for message in messages
    )
