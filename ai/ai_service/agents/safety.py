"""Deterministic red-flag emergency detection (CLAUDE.md §6.2 ``safety_check``, §7.2).

No LLM is involved: the patterns in ``safety_terms.yaml`` are compiled once at startup, so a
missing file or a bad regex stops the service instead of silently disabling the check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

TERMS_PATH = Path(__file__).resolve().parent / "safety_terms.yaml"


@dataclass(frozen=True)
class Category:
    name: str
    patterns: tuple[re.Pattern[str], ...]
    examples: tuple[str, ...]


class SafetyChecker:
    def __init__(self, categories: list[Category]) -> None:
        if not categories:
            raise ValueError("safety terms: no categories defined")
        self.categories = categories

    @classmethod
    def load(cls, path: Path = TERMS_PATH) -> SafetyChecker:
        """Read and compile the terms file. Anything malformed raises ``ValueError``."""
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw = data.get("categories") if isinstance(data, dict) else None
        if not isinstance(raw, list) or not raw:
            raise ValueError(f"safety terms: {path.name} has no 'categories' list")
        categories: list[Category] = []
        for entry in raw:
            name = str(entry.get("name", "")).strip()
            patterns = entry.get("patterns") or []
            if not name or not patterns:
                raise ValueError(f"safety terms: category {name or entry!r} needs name + patterns")
            try:
                compiled = tuple(re.compile(p, re.IGNORECASE) for p in patterns)
            except re.error as exc:
                raise ValueError(f"safety terms: invalid regex in '{name}': {exc}") from exc
            categories.append(Category(name, compiled, tuple(entry.get("examples") or ())))
        return cls(categories)

    def check(self, text: str) -> list[str]:
        """Names of every category whose pattern matches ``text`` (empty means no red flag)."""
        return [c.name for c in self.categories if any(p.search(text) for p in c.patterns)]
