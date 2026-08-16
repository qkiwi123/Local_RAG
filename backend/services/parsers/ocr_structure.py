"""OCR 文本的确定性结构分类。

只给 OCR 段落“贴结构类型”，绝不改写文本：输入段落集合与输出 BlockDraft
文本集合一一对应，因此 canonical_text 保持不变，unit/chunk/向量无需重建。

规则顺序（保守优先）：
  R1 第X章/节/部分/篇        -> heading
  R2 一、二、... 开头短句      -> heading
  R3 1 / 1.1 / 1.1.1 开头短句  -> heading
  R4 项目符号或数字列表       -> list_item
  其他                       -> paragraph
"""

from __future__ import annotations

import re

from backend.services.parsers.assembler import BlockDraft
from backend.services.parsers.text import split_paragraphs

_STRUCTURE_VERSION = "heuristic-v1"
_HEADING_MAX_CHARS = 40
_SENTENCE_ENDINGS = ("。", "！", "？", "；", "!", "?", ";")

_R1 = re.compile(r"^第[一二三四五六七八九十百\d]+[章节部分篇](?:\s+\S+)?$")
_R2 = re.compile(r"^[一二三四五六七八九十]+、\s*\S+")
_R3 = re.compile(r"^\d+(?:\.\d+){0,2}[、.．\s]\s*\S+")
_BULLET = re.compile(r"^[·•\-*]\s*\S+")
_NUMBERED_ITEM = re.compile(r"^(?:[（(]?\d+[）).、]|[-*])\s*\S+")


def _heading_level(text: str) -> int | None:
    if _R1.match(text):
        return 1
    if _R2.match(text):
        return 1
    match = _R3.match(text)
    if match:
        prefix = re.match(r"^\d+(?:\.\d+){0,2}", text)
        if prefix is None:
            return 1
        return min(2, prefix.group(0).count(".") + 1)
    return None


def _looks_like_heading(text: str) -> bool:
    stripped = text.strip()
    if not stripped or len(stripped) > _HEADING_MAX_CHARS:
        return False
    if stripped.endswith(_SENTENCE_ENDINGS):
        return False
    return True


def classify_ocr_blocks(
    text: str,
    *,
    ocr_attrs: dict[str, str | int | bool] | None = None,
    allow_headings: bool = True,
    allow_lists: bool = True,
    split_into_paragraphs: bool = True,
) -> list[BlockDraft]:
    """把 OCR 文本拆成带结构类型的 blocks；无法判断时回退 paragraph。

    返回的 block.text 与 ``split_paragraphs(text)`` 完全一致，仅类型/层级不同；
    ``split_into_paragraphs=False`` 时保持原文作为单个 block（避免规范化改写）。
    """
    paragraphs = (
        split_paragraphs(text)
        if split_into_paragraphs
        else ([text] if text and text.strip() else [])
    )
    base_attrs = dict(ocr_attrs or {})
    base_attrs["ocr_structure"] = _STRUCTURE_VERSION
    drafts: list[BlockDraft] = []

    for paragraph in paragraphs:
        stripped = paragraph.strip()
        lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
        attrs = dict(base_attrs)

        # 单行且像标题：整段升级为 heading。
        heading_level = (
            _heading_level(stripped) if allow_headings else None
        )
        if (
            heading_level is not None
            and len(lines) == 1
            and _looks_like_heading(stripped)
        ):
            drafts.append(
                BlockDraft(
                    block_type="heading",
                    text=paragraph,
                    heading_level=heading_level,
                    attributes=attrs,
                )
            )
            continue

        # 多行段落但首行像标题：文本不能改写，保留 paragraph，
        # 通过属性告知渲染器首行应按标题展示。
        if heading_level is not None and len(lines) > 1 and _looks_like_heading(lines[0]):
            attrs["ocr_leading_heading_level"] = heading_level
            drafts.append(
                BlockDraft(
                    block_type="paragraph",
                    text=paragraph,
                    attributes=attrs,
                )
            )
            continue

        if allow_lists and lines and all(
            _BULLET.match(line) or _NUMBERED_ITEM.match(line) for line in lines
        ):
            drafts.append(
                BlockDraft(
                    block_type="list_item",
                    text=paragraph,
                    list_level=0,
                    attributes=attrs,
                )
            )
            continue

        drafts.append(
            BlockDraft(
                block_type="paragraph",
                text=paragraph,
                attributes=attrs,
            )
        )

    return drafts
