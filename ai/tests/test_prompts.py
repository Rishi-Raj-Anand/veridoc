"""Prompt templates are real files that load (CLAUDE.md §14)."""

from __future__ import annotations

import pytest

from ai_service.agents.citations import NOT_IN_DOCUMENTS
from ai_service.prompts import PROMPTS_DIR, load_prompt


def test_every_prompt_file_loads_and_is_not_empty() -> None:
    files = sorted(PROMPTS_DIR.glob("*.md"))

    assert files
    for path in files:
        assert load_prompt(path.stem)


def test_generator_prompt_carries_the_contract() -> None:
    prompt = load_prompt("generator")

    assert NOT_IN_DOCUMENTS in prompt
    assert "[n]" in prompt
    assert "pharmacist" in prompt


def test_missing_prompt_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_prompt("does-not-exist")
