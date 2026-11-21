"""Parent-child chunking (CLAUDE.md §6.1.4).

Parent chunks (~800 tokens) are what the Generator reads on escalation; child chunks
(~300 tokens, 50 overlap) are what gets embedded and searched. Every child carries its
``parent_id``. Tokens are estimated as ``len(text) / 4`` - no tokenizer dependency.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from ai_service.config import Settings
from ai_service.ingestion.parsers import PageText

CHARS_PER_TOKEN = 4
PARAGRAPH_SEPARATOR = "\n\n"
HEADING_MAX_CHARS = 100
# A trailing parent smaller than this fraction of the target is folded into its
# predecessor, as long as the result stays within MERGE_CEILING x the target.
TINY_PARENT_FRACTION = 0.25
MERGE_CEILING = 1.25

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


@dataclass(frozen=True)
class ParentChunk:
    parent_id: str
    index: int
    page_start: int
    page_end: int
    text: str


@dataclass(frozen=True)
class ChildChunk:
    parent_id: str
    chunk_index: int
    page: int
    text: str


@dataclass(frozen=True)
class _Block:
    page: int
    text: str

    @property
    def is_heading(self) -> bool:
        stripped = self.text.strip()
        if "\n" in stripped or len(stripped) > HEADING_MAX_CHARS:
            return False
        return stripped.startswith("#") or not stripped.endswith((".", "!", "?", ":", ";", ","))


def make_parent_id(doc_id: int, index: int) -> str:
    return f"{doc_id}:p{index:05d}"


def _hard_split(text: str, max_chars: int) -> list[str]:
    """Split at whitespace so no piece exceeds ``max_chars`` (last resort)."""
    pieces: list[str] = []
    remaining = text.strip()
    while len(remaining) > max_chars:
        cut = remaining.rfind(" ", 0, max_chars)
        if cut <= 0:
            cut = max_chars
        pieces.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        pieces.append(remaining)
    return pieces


def split_oversize(text: str, max_chars: int) -> list[str]:
    """Split an over-long block on sentence boundaries, then hard at whitespace."""
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(text):
        if len(sentence) > max_chars:
            if current:
                pieces.append(current)
                current = ""
            pieces.extend(_hard_split(sentence, max_chars))
            continue
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) > max_chars:
            pieces.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def _blocks(pages: list[PageText], max_chars: int) -> list[_Block]:
    blocks: list[_Block] = []
    for page in pages:
        for paragraph in re.split(r"\n\s*\n", page.text):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            blocks.extend(
                _Block(page.page, piece) for piece in split_oversize(paragraph, max_chars)
            )
    return blocks


def _join(blocks: list[_Block]) -> str:
    return PARAGRAPH_SEPARATOR.join(block.text for block in blocks)


def _pack_parents(blocks: list[_Block], target_tokens: int) -> list[list[_Block]]:
    """Greedily pack blocks into parents, keeping headings with what follows them."""
    groups: list[list[_Block]] = []
    current: list[_Block] = []

    def size(items: list[_Block]) -> int:
        return estimate_tokens(_join(items)) if items else 0

    for block in blocks:
        if current and (
            size([*current, block]) > target_tokens
            or (block.is_heading and size(current) >= target_tokens // 2)
        ):
            carried: list[_Block] = []
            if len(current) > 1 and current[-1].is_heading:
                carried = [current.pop()]
            groups.append(current)
            current = carried
        current.append(block)
    if current:
        groups.append(current)

    if len(groups) > 1:
        last, previous = groups[-1], groups[-2]
        if (
            size(last) < target_tokens * TINY_PARENT_FRACTION
            and size([*previous, *last]) <= target_tokens * MERGE_CEILING
        ):
            groups[-2] = [*previous, *last]
            groups.pop()
    return groups


def _page_at(blocks: list[_Block], offset: int) -> int:
    """Page of the block containing character ``offset`` of the joined parent text."""
    position = 0
    for block in blocks:
        end = position + len(block.text)
        if offset < end + len(PARAGRAPH_SEPARATOR):
            return block.page
        position = end + len(PARAGRAPH_SEPARATOR)
    return blocks[-1].page


def _child_windows(text: str, size_chars: int, overlap_chars: int) -> list[tuple[int, int]]:
    """Sliding windows over ``text``, with ends snapped back to whitespace."""
    if len(text) <= size_chars:
        return [(0, len(text))]
    windows: list[tuple[int, int]] = []
    start = 0
    while start < len(text):
        end = min(start + size_chars, len(text))
        if end < len(text):
            snapped = text.rfind(" ", start + size_chars // 2, end)
            if snapped == -1:
                snapped = text.rfind("\n", start + size_chars // 2, end)
            if snapped != -1:
                end = snapped
        windows.append((start, end))
        if end >= len(text):
            break
        next_start = max(end - overlap_chars, start + 1)
        # begin the next window on a word boundary rather than mid-word
        boundary = text.find(" ", next_start, end)
        start = boundary + 1 if boundary != -1 else next_start
    return windows


def chunk_document(
    doc_id: int, pages: list[PageText], settings: Settings
) -> tuple[list[ParentChunk], list[ChildChunk]]:
    """Split parsed pages into parent chunks and their linked child chunks."""
    parent_chars = settings.parent_chunk_tokens * CHARS_PER_TOKEN
    child_chars = settings.child_chunk_tokens * CHARS_PER_TOKEN
    overlap_chars = settings.child_chunk_overlap_tokens * CHARS_PER_TOKEN

    blocks = _blocks(pages, parent_chars)
    parents: list[ParentChunk] = []
    children: list[ChildChunk] = []
    for index, group in enumerate(_pack_parents(blocks, settings.parent_chunk_tokens)):
        parent_id = make_parent_id(doc_id, index)
        text = _join(group)
        parents.append(
            ParentChunk(
                parent_id=parent_id,
                index=index,
                page_start=group[0].page,
                page_end=group[-1].page,
                text=text,
            )
        )
        for start, end in _child_windows(text, child_chars, overlap_chars):
            piece = text[start:end].strip()
            if piece:
                children.append(
                    ChildChunk(
                        parent_id=parent_id,
                        chunk_index=len(children),
                        page=_page_at(group, start),
                        text=piece,
                    )
                )
    return parents, children
