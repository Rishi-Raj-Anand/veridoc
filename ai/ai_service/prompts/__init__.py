"""Prompt templates (CLAUDE.md §14): Markdown files, loaded once at startup."""

from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent


def load_prompt(name: str) -> str:
    """Read ``prompts/<name>.md``. A missing or empty file raises, so startup fails loudly."""
    text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"prompt '{name}' is empty")
    return text
