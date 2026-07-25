"""Markdown adapter: CommonMark tokens -> ParsedUnit。"""

from __future__ import annotations

from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.token import Token

from backend.models.document_structure import ParsedUnit, SourceLocator
from backend.services.parsers.assembler import BlockDraft, assemble_unit_from_drafts
from backend.services.parsers.tables import render_table_markdown
from backend.services.parsers.text import normalize_text


MAX_SECTION_CHARS = 12_000


def _strip_front_matter(source: str) -> str:
    """移除文件开头的 YAML front matter，不把配置项送入检索正文。"""
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return source
    for index in range(1, len(lines)):
        if lines[index].strip() in {"---", "..."}:
            return "\n".join(lines[index + 1:])
    return source


def _render_inline(children: list[Token] | None) -> str:
    """提取安全、确定性的可见文本，并在链接文字后保留 URL。"""
    tokens = children or []

    def render_range(start: int, stop: int) -> str:
        output: list[str] = []
        index = start
        while index < stop:
            token = tokens[index]
            if token.type == "link_open":
                depth = 1
                closing = index + 1
                while closing < stop and depth:
                    if tokens[closing].type == "link_open":
                        depth += 1
                    elif tokens[closing].type == "link_close":
                        depth -= 1
                    closing += 1
                visible = render_range(index + 1, max(index + 1, closing - 1))
                url = token.attrGet("href") or ""
                output.append(visible)
                if url and url != visible:
                    output.append(f" ({url})")
                index = closing
                continue
            if token.type == "image":
                output.append(token.content)
            elif token.type in {"text", "code_inline", "html_inline"}:
                output.append(token.content)
            elif token.type in {"softbreak", "hardbreak"}:
                output.append("\n")
            index += 1
        return "".join(output)

    return normalize_text(render_range(0, len(tokens)))


def _parse_table(tokens: list[Token], start: int) -> tuple[BlockDraft, int]:
    rows: list[list[str]] = []
    row: list[str] | None = None
    index = start + 1
    while index < len(tokens) and tokens[index].type != "table_close":
        token = tokens[index]
        if token.type == "tr_open":
            row = []
        elif token.type == "inline" and row is not None:
            row.append(_render_inline(token.children))
        elif token.type == "tr_close" and row is not None:
            rows.append(row)
            row = None
        index += 1

    if not rows:
        raise ValueError("Markdown table 未解析出任何单元格")
    return (
        BlockDraft(
            block_type="table",
            text=render_table_markdown(rows),
            table_rows=rows,
        ),
        index + 1,
    )


def _updated_heading_path(
    heading_path: list[str],
    heading_level: int,
    text: str,
) -> list[str]:
    return [*heading_path[: max(heading_level - 1, 0)], text]


def _block_drafts(source: str) -> list[tuple[BlockDraft, list[str], bool]]:
    """返回 (draft, 当前 heading_path, 是否开启一级/二级 section)。"""
    tokens = MarkdownIt("commonmark").enable("table").parse(source)
    output: list[tuple[BlockDraft, list[str], bool]] = []
    heading_path: list[str] = []
    list_stack: list[bool] = []
    blockquote_depth = 0
    index = 0

    while index < len(tokens):
        token = tokens[index]
        if token.type == "heading_open":
            level = int(token.tag[1:])
            inline = tokens[index + 1]
            text = _render_inline(inline.children)
            if text:
                heading_path = _updated_heading_path(heading_path, level, text)
                output.append((
                    BlockDraft(
                        block_type="heading",
                        text=text,
                        heading_level=level,
                    ),
                    list(heading_path),
                    level <= 2,
                ))
            index += 3
            continue

        if token.type in {"bullet_list_open", "ordered_list_open"}:
            list_stack.append(token.type == "ordered_list_open")
        elif token.type in {"bullet_list_close", "ordered_list_close"}:
            if list_stack:
                list_stack.pop()
        elif token.type == "blockquote_open":
            blockquote_depth += 1
        elif token.type == "blockquote_close":
            blockquote_depth = max(0, blockquote_depth - 1)
        elif token.type in {"fence", "code_block"}:
            text = token.content.rstrip("\n")
            if text.strip():
                attributes = {}
                language = token.info.strip().split(maxsplit=1)[0] if token.info else ""
                if language:
                    attributes["language"] = language
                output.append((
                    BlockDraft(
                        block_type="code",
                        text=text,
                        attributes=attributes,
                    ),
                    list(heading_path),
                    False,
                ))
        elif token.type == "table_open":
            draft, index = _parse_table(tokens, index)
            output.append((draft, list(heading_path), False))
            continue
        elif token.type == "inline":
            previous_type = tokens[index - 1].type if index else ""
            if previous_type == "paragraph_open":
                text = _render_inline(token.children)
                if text:
                    if list_stack:
                        draft = BlockDraft(
                            block_type="list_item",
                            text=text,
                            list_level=len(list_stack) - 1,
                            attributes={"ordered": list_stack[-1]},
                        )
                    elif blockquote_depth:
                        draft = BlockDraft(block_type="quote", text=text)
                    else:
                        draft = BlockDraft(block_type="paragraph", text=text)
                    output.append((draft, list(heading_path), False))
        elif token.type == "html_block":
            text = normalize_text(token.content)
            if text:
                output.append((
                    BlockDraft(block_type="paragraph", text=text),
                    list(heading_path),
                    False,
                ))
        index += 1

    return output


def parse_markdown_units(
    file_path: str,
    *,
    document_id: str,
    document_name: str | None = None,
) -> list[ParsedUnit]:
    """按一级/二级标题优先分 section，超长 section 再确定性切为多个 unit。"""
    name = document_name or Path(file_path).name
    source = Path(file_path).read_text(encoding="utf-8")
    parsed = _block_drafts(_strip_front_matter(source))
    if not parsed:
        return []

    sections: list[tuple[list[str], list[BlockDraft]]] = []
    drafts: list[BlockDraft] = []
    unit_heading_path: list[str] = []
    char_count = 0

    def flush() -> None:
        nonlocal drafts, unit_heading_path, char_count
        if drafts:
            sections.append((list(unit_heading_path), drafts))
        drafts = []
        unit_heading_path = []
        char_count = 0

    for draft, heading_path, starts_section in parsed:
        next_size = char_count + (2 if drafts else 0) + len(draft.text)
        if drafts and (starts_section or next_size > MAX_SECTION_CHARS):
            flush()
        if not drafts:
            unit_heading_path = list(heading_path)
        drafts.append(draft)
        char_count += (2 if len(drafts) > 1 else 0) + len(draft.text)
    flush()

    units: list[ParsedUnit] = []
    for unit_index, (heading_path, section_drafts) in enumerate(sections):
        label = (
            "章节：" + " / ".join(heading_path)
            if heading_path
            else f"第 {unit_index + 1} 节"
        )
        units.append(
            assemble_unit_from_drafts(
                document_id=document_id,
                document_name=name,
                locator=SourceLocator(
                    kind="section",
                    unit_index=unit_index,
                    location_label=label,
                    heading_path=heading_path,
                ),
                drafts=section_drafts,
            )
        )
    return units
