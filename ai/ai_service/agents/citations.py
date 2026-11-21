"""Pure helpers for ``[n]`` citation markers in a draft answer."""

from __future__ import annotations

import re

NOT_IN_DOCUMENTS = "NOT_IN_DOCUMENTS"

# ``[1]``, ``[1][2]``, ``[1, 2]`` and ``[1,2,3]`` - digits and commas only.
_MARKER = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_thinking(text: str) -> str:
    """Drop any ``<think>`` block (Qwen3 can emit an empty one even with ``think=False``)."""
    return _THINK.sub("", text).strip()


def is_not_covered(text: str) -> bool:
    """True when the Generator said the sources do not answer the question at all."""
    return text.strip().strip("`*_. ") == NOT_IN_DOCUMENTS


def extract_citation_numbers(text: str) -> list[int]:
    """Every cited number in order of appearance, without duplicates."""
    seen: list[int] = []
    for match in _MARKER.finditer(text):
        for part in match.group(1).split(","):
            number = int(part)
            if number not in seen:
                seen.append(number)
    return seen


def clean_citations(text: str, valid: set[int]) -> tuple[str, list[int]]:
    """Remove markers that point at no source; return the text and the valid numbers used.

    A marker such as ``[1, 7]`` keeps its valid numbers and loses the invalid one.
    """

    def keep_valid(match: re.Match[str]) -> str:
        numbers = [int(p) for p in match.group(1).split(",")]
        kept = [n for n in numbers if n in valid]
        return f"[{', '.join(str(n) for n in kept)}]" if kept else ""

    cleaned = _MARKER.sub(keep_valid, text)
    cleaned = re.sub(r"[ \t]+([.,;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    return cleaned, [n for n in extract_citation_numbers(cleaned) if n in valid]
