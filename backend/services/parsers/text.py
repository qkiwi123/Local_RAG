"""Parser adapter 共用的保守文本规范化工具。"""

from __future__ import annotations

import re


_FULLWIDTH_ALNUM_TRANSLATION = str.maketrans(
    {
        codepoint: codepoint - 0xFEE0
        for start, end in (
            (0xFF10, 0xFF19),
            (0xFF21, 0xFF3A),
            (0xFF41, 0xFF5A),
        )
        for codepoint in range(start, end + 1)
    }
)


def normalize_text(text: str) -> str:
    """统一全角字母数字、换行和横向空白，不改写语义内容。"""
    if not text:
        return ""

    normalized = text.translate(_FULLWIDTH_ALNUM_TRANSLATION)
    normalized = normalized.replace("\u3000", " ")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    lines = [
        re.sub(r"[^\S\r\n]+", " ", line).strip()
        for line in normalized.split("\n")
    ]

    compacted: list[str] = []
    previous_blank = False
    for line in lines:
        if line:
            compacted.append(line)
            previous_blank = False
        elif compacted and not previous_blank:
            compacted.append("")
            previous_blank = True
    return "\n".join(compacted).strip()


def split_paragraphs(text: str) -> list[str]:
    """按规范文本中的空行拆分段落并过滤空段。"""
    normalized = normalize_text(text)
    return [part.strip() for part in normalized.split("\n\n") if part.strip()]
