"""Intent routing helpers (CLAUDE.md §6.9): greeting fast path and the LLM classifier."""

from __future__ import annotations

import pytest

from ai_service.agents.classifier import IntentClassifier, is_greeting
from ai_service.config import Settings
from tests.helpers import FakeLlm, intent_json


@pytest.mark.parametrize(
    "message",
    [
        "hi",
        "Hi!",
        "hello there",
        "hey doc 👋",
        "Good morning",
        "thanks!",
        "Thank you so much.",
        "ok",
        "bye",
        "hi, how are you?",
        "who are you?",
        "what can you do",
        "thanks bye",
    ],
)
def test_plain_greetings_take_the_fast_path(message: str) -> None:
    assert is_greeting(message)


@pytest.mark.parametrize(
    "message",
    [
        "hi, what is malaria?",
        "hello I have a fever",
        "thanks, and what about children?",
        "how are you treating migraine",
        "write my essay",
        "chest pain",
        "",
        "   ",
        "hi " * 30,
    ],
)
def test_anything_with_content_goes_to_the_classifier(message: str) -> None:
    assert not is_greeting(message)


def classifier(llm: FakeLlm) -> IntentClassifier:
    return IntentClassifier(llm, Settings(_env_file=None))


def test_a_valid_reply_is_returned_at_temperature_zero_with_a_schema() -> None:
    llm = FakeLlm(classify_replies=[intent_json("out_of_scope")])

    assert classifier(llm).classify("give me a pasta recipe", [], "health") == "out_of_scope"
    call = llm.classify_calls[0]
    assert call["temperature"] == 0.0
    assert call["format"]["properties"]["intent"]["enum"] == [
        "medical",
        "smalltalk",
        "out_of_scope",
    ]


def test_malformed_json_is_retried_once_then_counts_as_medical() -> None:
    llm = FakeLlm(classify_replies=["not json", '{"intent": "banana"}'])

    assert classifier(llm).classify("???", [], "health") == "medical"
    assert len(llm.classify_calls) == 2


def test_a_retry_that_succeeds_is_used() -> None:
    llm = FakeLlm(classify_replies=["oops", intent_json("smalltalk")])

    assert classifier(llm).classify("nice to meet you", [], "health") == "smalltalk"


def test_the_scope_and_only_the_last_exchange_reach_the_prompt() -> None:
    llm = FakeLlm(classify_replies=[intent_json("medical")])
    history = [
        {"role": "user", "content": "OLDEST question"},
        {"role": "assistant", "content": "OLDEST answer"},
        {"role": "user", "content": "How is migraine treated?"},
        {"role": "assistant", "content": "Rest in a dark room [1]."},
    ]

    classifier(llm).classify("what about for kids?", history, "Only skin conditions.")

    user = llm.classify_calls[0]["messages"][1]["content"]
    assert "In-scope topics: Only skin conditions." in user
    assert "How is migraine treated?" in user
    assert "OLDEST" not in user
    assert user.endswith("Message to classify: what about for kids?")
