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
# A Markdown table's second line: ``| --- | :---: |``.
_TABLE_SEPARATOR = re.compile(r"^\s*\|(\s*:?-+:?\s*\|)+\s*$")


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
    is_table: bool = False
    # For a table block: the heading folded into it (``text`` then starts with it).
    title: str = ""

    @property
    def table_body(self) -> str:
        """The table lines alone, without the folded-in title."""
        return self.text[len(self.title) + 2 :] if self.title else self.text

    @property
    def is_heading(self) -> bool:
        if self.is_table:
            return False
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


def _split_tables(paragraph: str) -> list[tuple[str, bool]]:
    """Split a paragraph into ``(text, is_table)`` runs.

    A Markdown table is a run of ``|`` lines whose second line is a separator row. Anything
    else - including pipe-lines without a separator - stays plain text.
    """
    runs: list[tuple[str, bool]] = []
    plain: list[str] = []
    lines = paragraph.split("\n")
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("|"):
            j = i
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            group = lines[i:j]
            if len(group) >= 2 and _TABLE_SEPARATOR.match(group[1]):
                if plain:
                    runs.append(("\n".join(plain), False))
                    plain = []
                runs.append(("\n".join(group), True))
            else:
                plain.extend(group)
            i = j
        else:
            plain.append(lines[i])
            i += 1
    if plain:
        runs.append(("\n".join(plain), False))
    return runs


def split_table(table: str, max_chars: int, title: str = "") -> list[str]:
    """Split a Markdown table on row boundaries, repeating the header in every piece.

    Each piece is a complete, valid table (``title`` + header + separator + whole rows), so a
    chunk never starts mid-row and never loses the column names. A single row too long for
    the budget is cut at whitespace rather than dropped.
    """
    lines = table.split("\n")
    header = "\n".join(lines[:2])
    prefix = f"{title}\n\n{header}" if title else header
    rows = lines[2:]
    budget = max(max_chars - len(prefix) - 1, max_chars // 2)

    pieces: list[str] = []
    current: list[str] = []
    used = 0

    def flush() -> None:
        nonlocal current, used
        if current:
            pieces.append(prefix + "\n" + "\n".join(current))
        current, used = [], 0

    for row in rows:
        for part in _hard_split(row, budget) if len(row) > budget else [row]:
            if current and used + len(part) + 1 > budget:
                flush()
            current.append(part)
            used += len(part) + 1
    flush()
    return pieces or [prefix]


def _blocks(pages: list[PageText], max_chars: int) -> list[_Block]:
    blocks: list[_Block] = []
    for page in pages:
        for paragraph in re.split(r"\n\s*\n", page.text):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            for text, is_table in _split_tables(paragraph):
                if is_table:
                    # A heading right above the table goes *into* every table piece, so the
                    # section name survives however the table is split across chunks.
                    title = ""
                    if blocks and blocks[-1].page == page.page and blocks[-1].is_heading:
                        title = blocks.pop().text
                    blocks.extend(
                        _Block(page.page, piece, is_table=True, title=title)
                        for piece in split_table(text, max_chars, title)
                    )
                else:
                    blocks.extend(
                        _Block(page.page, piece) for piece in split_oversize(text, max_chars)
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


def _children_of_parent(
    group: list[_Block], child_chars: int, overlap_chars: int
) -> list[tuple[int, str]]:
    """``(page, text)`` children of one parent.

    Prose is windowed with overlap. A table is split on row boundaries with its header
    (and the heading folded into it, see ``_blocks``) repeated in every piece.
    """
    children: list[tuple[int, str]] = []
    run: list[_Block] = []

    def flush_run() -> None:
        if not run:
            return
        text = _join(run)
        for start, end in _child_windows(text, child_chars, overlap_chars):
            piece = text[start:end].strip()
            if piece:
                children.append((_page_at(run, start), piece))
        run.clear()

    for block in group:
        if not block.is_table:
            run.append(block)
            continue
        flush_run()
        children.extend(
            (block.page, piece) for piece in split_table(block.table_body, child_chars, block.title)
        )
    flush_run()
    return children


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
        for page, piece in _children_of_parent(group, child_chars, overlap_chars):
            children.append(
                ChildChunk(
                    parent_id=parent_id,
                    chunk_index=len(children),
                    page=page,
                    text=piece,
                )
            )
    return parents, children
