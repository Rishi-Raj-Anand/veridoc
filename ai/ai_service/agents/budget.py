"""Prompt-budget trimming (CLAUDE.md §6.4).

Trim history first, then the lowest-scoring context; the system prompt is never touched.
Tokens are estimated with the same ``len(text) / 4`` rule the chunker uses.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from ai_service.ingestion.chunker import CHARS_PER_TOKEN, estimate_tokens
from ai_service.retrieval.retriever import Source, format_context

logger = logging.getLogger(__name__)

_SEPARATOR_TOKENS = 1  # the blank line format_context puts between sources


def _source_tokens(source: Source) -> int:
    return estimate_tokens(format_context([source])) + _SEPARATOR_TOKENS


def trim_sources(sources: list[Source], max_tokens: int) -> list[Source]:
    """Drop the lowest-scoring sources until the context fits ``max_tokens``.

    ``Source.n`` follows score order, so dropping from the end keeps the numbering
    contiguous. At least one source always survives: if it alone is over budget its text is
    truncated rather than removed, because an empty context would make the draft ungrounded.
    """
    kept = list(sources)
    while len(kept) > 1 and sum(_source_tokens(s) for s in kept) > max_tokens:
        dropped = kept.pop()
        logger.info(
            "budget: dropped source [%d] %s p. %s", dropped.n, dropped.filename, dropped.page_label
        )
    if kept and _source_tokens(kept[0]) > max_tokens:
        header_chars = len(format_context([replace(kept[0], text="")]))
        room = max(max_tokens * CHARS_PER_TOKEN - header_chars, 0)
        logger.info("budget: truncated source [%d] to %d chars", kept[0].n, room)
        kept[0] = replace(kept[0], text=kept[0].text[:room].rstrip())
    return kept


def trim_history(
    messages: list[dict[str, str]], max_tokens: int, max_messages: int
) -> list[dict[str, str]]:
    """Keep the newest messages: at most ``max_messages`` and ``max_tokens`` in total."""
    kept = list(messages[-max_messages:]) if max_messages > 0 else []
    while kept and sum(estimate_tokens(m["content"]) for m in kept) > max_tokens:
        kept.pop(0)
    return kept
