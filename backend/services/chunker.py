"""Token-aware recursive text chunking with stable source positions.

The recursive strategy keeps parser page boundaries and chooses split points in
this order: paragraph -> sentence -> clause -> character fallback. Character
size is a retrieval target; ``max_tokens`` is a hard encoder safety ceiling.
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from collections.abc import Callable
from typing import List

from backend.models.schemas import ChunkMetadata, DocumentChunk


TokenCounter = Callable[[str], int]
Span = tuple[int, int]

_BOUNDARY_PATTERNS = (
    re.compile(r"\n{2,}"),
    re.compile(r"[。！？；\n]+"),
    re.compile(r"[，、]+"),
)


def _validate_parameters(
    chunk_size: int,
    overlap: int,
    max_tokens: int | None,
) -> None:
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须大于 0")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap 必须满足 0 <= overlap < chunk_size")
    if max_tokens is not None and max_tokens <= 0:
        raise ValueError("max_tokens 必须大于 0")


def _collect_boundaries(text: str) -> tuple[list[int], ...]:
    """Return delimiter end offsets, grouped by semantic priority."""
    return tuple(
        sorted({match.end() for match in pattern.finditer(text)})
        for pattern in _BOUNDARY_PATTERNS
    )


def _positions_between(positions: list[int], start: int, end: int) -> list[int]:
    left = bisect_left(positions, start)
    right = bisect_right(positions, end)
    return positions[left:right]


def _max_fitting_end(
    text: str,
    start: int,
    char_end: int,
    *,
    max_tokens: int | None,
    token_counter: TokenCounter,
) -> int:
    """Find the farthest prefix end that satisfies the hard token ceiling."""
    if max_tokens is None or token_counter(text[start:char_end]) <= max_tokens:
        return char_end

    low, high = start + 1, char_end
    best = start
    while low <= high:
        middle = (low + high) // 2
        if token_counter(text[start:middle]) <= max_tokens:
            best = middle
            low = middle + 1
        else:
            high = middle - 1

    if best == start:
        raise ValueError("max_tokens 过小，单个字符也无法放入 chunk")
    return best


def _choose_semantic_end(
    boundaries: tuple[list[int], ...],
    start: int,
    hard_end: int,
    text_end: int,
    chunk_size: int,
) -> int:
    """Choose the highest-priority useful boundary before ``hard_end``."""
    if hard_end >= text_end:
        return text_end

    # Ignore very early delimiters so short clauses/paragraphs are merged into
    # a useful retrieval unit. One third of the target is the soft minimum.
    minimum_length = min(max(1, chunk_size // 3), hard_end - start)
    minimum_end = start + minimum_length
    for positions in boundaries:
        candidates = _positions_between(positions, minimum_end, hard_end)
        if candidates:
            return candidates[-1]

    # If the token ceiling is unusually tight, even an early semantic boundary
    # is preferable to cutting a sentence at the hard limit.
    fallback_candidates = [
        position
        for positions in boundaries
        for position in _positions_between(positions, start + 1, hard_end)
    ]
    return max(fallback_candidates, default=hard_end)


def _split_fixed_spans(text: str, chunk_size: int, overlap: int) -> list[Span]:
    spans = []
    start = 0
    step = chunk_size - overlap
    while start < len(text):
        spans.append((start, min(start + chunk_size, len(text))))
        start += step
    return spans


def _split_recursive_spans(
    text: str,
    chunk_size: int,
    overlap: int,
    *,
    max_tokens: int | None,
    token_counter: TokenCounter,
) -> list[Span]:
    boundaries = _collect_boundaries(text)
    spans: list[Span] = []
    start = 0

    while start < len(text):
        char_end = min(start + chunk_size, len(text))
        hard_end = _max_fitting_end(
            text,
            start,
            char_end,
            max_tokens=max_tokens,
            token_counter=token_counter,
        )
        end = _choose_semantic_end(
            boundaries,
            start,
            hard_end,
            len(text),
            chunk_size,
        )
        if end <= start:
            raise RuntimeError("切分器未能向前推进")
        spans.append((start, end))
        if end == len(text):
            break
        start = end if overlap == 0 else max(start + 1, end - overlap)

    return spans


def _split_text_with_offsets(
    text: str,
    chunk_size: int,
    overlap: int,
    *,
    max_tokens: int | None = None,
    token_counter: TokenCounter | None = None,
    strategy: str = "recursive",
) -> list[Span]:
    """Split text and return half-open offsets into the original string."""
    _validate_parameters(chunk_size, overlap, max_tokens)
    if not text or not text.strip():
        return []

    if strategy == "fixed":
        return _split_fixed_spans(text, chunk_size, overlap)
    if strategy != "recursive":
        raise ValueError(f"不支持的 chunk strategy: {strategy}")

    counter = token_counter or len
    return _split_recursive_spans(
        text,
        chunk_size,
        overlap,
        max_tokens=max_tokens,
        token_counter=counter,
    )


def _split_text(
    text: str,
    chunk_size: int,
    overlap: int,
    *,
    max_tokens: int | None = None,
    token_counter: TokenCounter | None = None,
    strategy: str = "recursive",
) -> List[str]:
    """Compatibility wrapper returning chunk contents without metadata."""
    spans = _split_text_with_offsets(
        text,
        chunk_size,
        overlap,
        max_tokens=max_tokens,
        token_counter=token_counter,
        strategy=strategy,
    )
    return [text[start:end] for start, end in spans]


def chunk_document(
    pages: List[dict],
    chunk_size: int,
    overlap: int,
    *,
    max_tokens: int | None = None,
    token_counter: TokenCounter | None = None,
    strategy: str = "recursive",
) -> List[DocumentChunk]:
    """Split parsed pages and attach stable, source-addressable metadata.

    DOCX parsing can yield several content blocks with one virtual page number.
    Those blocks form a logical page separated by ``\n\n`` for offset purposes;
    ``block_index`` additionally identifies the original parser block.
    """
    _validate_parameters(chunk_size, overlap, max_tokens)
    counter = token_counter or len
    all_chunks: List[DocumentChunk] = []
    page_chunk_offsets: dict[tuple[str, int], int] = {}
    page_char_offsets: dict[tuple[str, int], int] = {}
    page_block_offsets: dict[tuple[str, int], int] = {}

    for page in pages:
        text = page.get("text", "")
        page_number = page.get("page_number", 1)
        document_name = page.get("document_name", "unknown")
        document_id = page.get("document_id", document_name)
        content_type = page.get("content_type", "paragraph")
        page_key = (document_id, page_number)
        block_index = page_block_offsets.get(page_key, 0)
        page_start = page_char_offsets.get(page_key, 0)

        spans = _split_text_with_offsets(
            text,
            chunk_size,
            overlap,
            max_tokens=max_tokens,
            token_counter=counter,
            strategy=strategy,
        )
        chunk_index = page_chunk_offsets.get(page_key, 0)
        for local_start, local_end in spans:
            chunk_text = text[local_start:local_end]
            if not chunk_text.strip():
                continue
            start_char = page_start + local_start
            end_char = page_start + local_end
            stable_id = (
                f"{document_id}_p{page_number}_b{block_index}"
                f"_s{start_char}_e{end_char}"
            )
            all_chunks.append(
                DocumentChunk(
                    content=chunk_text,
                    metadata=ChunkMetadata(
                        document_id=document_id,
                        document_name=document_name,
                        page_number=page_number,
                        chunk_index=chunk_index,
                        content_type=content_type,
                        chunk_id=stable_id,
                        block_index=block_index,
                        start_char=start_char,
                        end_char=end_char,
                        char_count=len(chunk_text),
                        token_count=counter(chunk_text),
                    ),
                )
            )
            chunk_index += 1

        page_chunk_offsets[page_key] = chunk_index
        page_char_offsets[page_key] = page_start + len(text) + 2
        page_block_offsets[page_key] = block_index + 1

    return all_chunks
