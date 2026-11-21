"""Needs a live Ollama with bge-m3 pulled: ``uv run pytest -m ollama``."""

from __future__ import annotations

import pytest

from ai_service.agents.verifier import Verifier
from ai_service.config import Settings
from ai_service.llm.ollama_client import OllamaClient
from ai_service.retrieval.retriever import Source

pytestmark = pytest.mark.ollama


def test_bge_m3_returns_1024_dim_vectors() -> None:
    vectors = OllamaClient(Settings(_env_file=None)).embed(["paracetamol for fever", "migraine"])

    assert len(vectors) == 2
    assert all(len(v) == 1024 for v in vectors)


def test_qwen3_chat_returns_text_without_thinking() -> None:
    reply = OllamaClient(Settings(_env_file=None)).chat(
        [{"role": "user", "content": "Reply with the single word: ready"}], temperature=0
    )

    assert reply.strip()
    assert "<think>" not in reply


SOURCES = [
    Source(
        n=1,
        filename="disease.md",
        page=1,
        text="Malaria: prevented with insecticide-treated bed nets and antimalarial prophylaxis.",
        score=0.9,
    )
]


def live_verifier() -> Verifier:
    settings = Settings(_env_file=None)
    return Verifier(OllamaClient(settings), settings)


def test_qwen3_verifier_returns_schema_valid_json_for_a_supported_draft() -> None:
    result = live_verifier().verify(
        "How is malaria prevented?", SOURCES, "Malaria is prevented with bed nets [1]."
    )

    assert not result.parse_failed  # the nullable missing_info survived Ollama's grammar
    assert 0.0 <= result.faithfulness <= 1.0
    assert 0.0 <= result.relevance <= 1.0


def test_qwen3_verifier_rejects_an_unsupported_dosage() -> None:
    result = live_verifier().verify(
        "How is malaria prevented?",
        SOURCES,
        "Malaria is prevented with bed nets [1]. Adults should take 500 mg of chloroquine "
        "daily [1].",
    )

    assert not result.parse_failed
    assert not result.passed


def test_qwen3_condenses_a_follow_up_into_a_standalone_question() -> None:
    from ai_service.agents.condenser import Condenser

    settings = Settings(_env_file=None)
    history = [
        {"role": "user", "content": "What is the treatment for migraine?"},
        {"role": "assistant", "content": "The references suggest rest and fluids [1]."},
    ]

    standalone = Condenser(OllamaClient(settings), settings).condense(
        history, "what about for children?"
    )

    assert "migraine" in standalone.lower()
    assert "child" in standalone.lower()


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("How are you doing today, Doctor AI?", "smalltalk"),
        ("Nice to meet you!", "smalltalk"),
        ("Give me a recipe for chocolate cake", "out_of_scope"),
        ("Help me write a Python function to sort a list", "out_of_scope"),
        ("What are the symptoms of malaria?", "medical"),
        ("hello, what helps with a migraine?", "medical"),
    ],
)
def test_qwen3_classifies_intent(message: str, expected: str) -> None:
    from ai_service.agents.classifier import IntentClassifier
    from ai_service.behaviour import DEFAULT_SCOPE

    settings = Settings(_env_file=None)
    classifier = IntentClassifier(OllamaClient(settings), settings)

    assert classifier.classify(message, [], DEFAULT_SCOPE) == expected


def test_qwen3_keeps_a_short_medical_follow_up_medical() -> None:
    from ai_service.agents.classifier import IntentClassifier
    from ai_service.behaviour import DEFAULT_SCOPE

    settings = Settings(_env_file=None)
    history = [
        {"role": "user", "content": "How is a migraine treated?"},
        {"role": "assistant", "content": "The references suggest rest in a dark, quiet room [1]."},
    ]
    intent = IntentClassifier(OllamaClient(settings), settings).classify(
        "what about for kids?", history, DEFAULT_SCOPE
    )

    assert intent == "medical"
