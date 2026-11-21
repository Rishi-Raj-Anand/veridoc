"""Condenser with a mocked LLM (CLAUDE.md §6.2, §6.4)."""

from __future__ import annotations

import pytest

from ai_service.agents.condenser import Condenser, clean_condensed
from ai_service.config import Settings
from tests.helpers import FakeLlm

HISTORY = [
    {"role": "user", "content": "What is the treatment for migraine?"},
    {"role": "assistant", "content": "The references suggest rest and hydration [1]."},
]
FOLLOW_UP = "what about for children?"
STANDALONE = "What is the treatment for migraine in children?"


def make(llm: FakeLlm, **cfg: int) -> Condenser:
    return Condenser(llm, Settings(_env_file=None, **cfg))


def test_the_first_message_passes_through_without_an_llm_call() -> None:
    llm = FakeLlm()

    assert make(llm).condense([], "How is malaria prevented?") == "How is malaria prevented?"
    assert llm.chat_calls == []


def test_a_follow_up_becomes_a_standalone_question() -> None:
    llm = FakeLlm(condense_replies=[STANDALONE])

    result = make(llm).condense(HISTORY, FOLLOW_UP)

    assert result == STANDALONE
    call = llm.condense_calls[0]
    assert call["temperature"] == 0.0
    assert call["format"] is None
    user = call["messages"][1]["content"]
    assert "User: What is the treatment for migraine?" in user
    assert "Assistant: The references suggest rest and hydration [1]." in user
    assert f"Latest message: {FOLLOW_UP}" in user


def test_history_is_trimmed_to_the_message_limit() -> None:
    llm = FakeLlm(condense_replies=[STANDALONE])
    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn-{i}"} for i in range(10)
    ]

    make(llm, history_max_messages=4).condense(history, FOLLOW_UP)

    user = llm.condense_calls[0]["messages"][1]["content"]
    assert "turn-5" not in user
    assert all(f"turn-{i}" in user for i in (6, 7, 8, 9))


def test_history_is_trimmed_to_the_token_budget_newest_first() -> None:
    llm = FakeLlm(condense_replies=[STANDALONE])
    history = [
        {"role": "user", "content": "old " * 400},  # ~400 tokens
        {"role": "assistant", "content": "recent answer"},
    ]

    make(llm, history_max_tokens=50).condense(history, FOLLOW_UP)

    user = llm.condense_calls[0]["messages"][1]["content"]
    assert "old old" not in user
    assert "recent answer" in user


@pytest.mark.parametrize("reply", ["", "   ", "NOT_IN_DOCUMENTS", "x" * 2001])
def test_an_unusable_rewrite_falls_back_to_the_raw_message(reply: str) -> None:
    llm = FakeLlm(condense_replies=[reply])

    assert make(llm).condense(HISTORY, FOLLOW_UP) == FOLLOW_UP


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('"What is X?"', "What is X?"),
        ("Standalone question: What is X?", "What is X?"),
        ("<think>hmm</think>\nWhat is X?", "What is X?"),
        ("“What   is\nX?”", "What is X?"),
    ],
)
def test_output_is_cleaned(raw: str, expected: str) -> None:
    assert clean_condensed(raw) == expected
