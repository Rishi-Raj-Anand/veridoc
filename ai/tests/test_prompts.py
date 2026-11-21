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


def test_verifier_prompt_names_every_schema_field() -> None:
    prompt = load_prompt("verifier")

    for field in ("faithfulness", "relevance", "unsupported_claims", "missing_info", "verdict"):
        assert f'"{field}"' in prompt


def test_missing_prompt_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_prompt("does-not-exist")


def test_condense_prompt_forbids_answering_and_demands_one_question() -> None:
    prompt = load_prompt("condense").lower()

    assert "standalone" in prompt
    assert "do not answer" in prompt or "never answer" in prompt
    assert "unchanged" in prompt


def test_condense_prompt_tells_subjectless_follow_ups_to_inherit_the_subject() -> None:
    """Live finding (5.7): 'what should I do now?' stayed unchanged without this rule, so an
    emergency in the earlier turn was never seen by the safety check."""
    prompt = load_prompt("condense").lower()

    assert "what should i do now?" in prompt
    assert "names no medical subject" in prompt
