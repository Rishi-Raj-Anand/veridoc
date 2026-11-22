"""§6.4 prompt budget (Phase 8.4): the worst-case Generator and Verifier prompts fit.

Worst case, estimated with the chunker's ``len(text) / 4`` rule:
- the fixed system prompt, plus the admin text at every cap (Generator only: the Verifier
  never sees admin text, §6.9);
- ``CONTEXT_MAX_TOKENS`` of sources, after ``trim_sources``;
- a 2,000-character question (the API maximum);
- a 1,000-token draft for the Verifier (the §6.4 answer budget).

The total must stay under ~7,000 tokens, leaving room in ``num_ctx = 8192`` for the reply.
The live check compares this with Ollama's real ``prompt_eval_count`` (CLAUDE.md §17).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_service.agents.budget import trim_sources
from ai_service.agents.generator import Generator
from ai_service.agents.verifier import Verifier
from ai_service.behaviour import (
    GUARDRAILS_MAX_CHARS,
    OUT_OF_SCOPE_MAX_CHARS,
    PERSONA_MAX_CHARS,
    SCOPE_MAX_CHARS,
    BehaviourSettings,
    BehaviourStore,
)
from ai_service.config import Settings
from ai_service.ingestion.chunker import estimate_tokens
from ai_service.retrieval.retriever import Source
from ai_service.routers.chat import MAX_MESSAGE_CHARS
from tests.helpers import verifier_json

PROMPT_BUDGET_TOKENS = 7000
ANSWER_BUDGET_TOKENS = 1000


class Capture:
    """Records the messages of every chat call and answers with a valid reply."""

    def __init__(self) -> None:
        self.messages: list[list[dict[str, str]]] = []

    def chat(self, messages: list[dict[str, str]], **_kwargs: Any) -> str:
        self.messages.append(list(messages))
        return verifier_json()


def prompt_tokens(messages: list[dict[str, str]]) -> int:
    return sum(estimate_tokens(m["content"]) for m in messages)


def worst_case_sources(settings: Settings) -> list[Source]:
    """More parent text than the budget allows, so trim_sources has to cut it to size."""
    raw = [
        Source(n=i, filename=f"reference-{i}.pdf", page=i, text="word " * 800, score=1 - i / 10)
        for i in range(1, 7)
    ]
    return trim_sources(raw, settings.context_max_tokens)


def maxed_behaviour(tmp_path: Path) -> BehaviourStore:
    store = BehaviourStore(tmp_path / "behaviour.json")
    store.update(
        BehaviourSettings(
            persona="p" * PERSONA_MAX_CHARS,
            scope="s" * SCOPE_MAX_CHARS,
            out_of_scope_message="o" * OUT_OF_SCOPE_MAX_CHARS,
            extra_guardrails="g" * GUARDRAILS_MAX_CHARS,
        ),
        "2026-11-22T00:00:00+00:00",
    )
    return store


def test_the_worst_case_generator_prompt_fits(tmp_path: Path) -> None:
    settings = Settings(_env_file=None)
    llm = Capture()
    generator = Generator(llm, settings, maxed_behaviour(tmp_path))  # type: ignore[arg-type]

    generator.generate("q" * MAX_MESSAGE_CHARS, worst_case_sources(settings))

    tokens = prompt_tokens(llm.messages[0])
    assert tokens < PROMPT_BUDGET_TOKENS, tokens
    assert tokens + ANSWER_BUDGET_TOKENS < settings.num_ctx


def test_the_worst_case_verifier_prompt_fits(tmp_path: Path) -> None:
    settings = Settings(_env_file=None)
    llm = Capture()
    verifier = Verifier(llm, settings)  # type: ignore[arg-type]
    draft = "d" * (ANSWER_BUDGET_TOKENS * 4)

    verifier.verify("q" * MAX_MESSAGE_CHARS, worst_case_sources(settings), draft)

    tokens = prompt_tokens(llm.messages[0])
    assert tokens < PROMPT_BUDGET_TOKENS, tokens
    assert tokens + 300 < settings.num_ctx  # the Verifier's JSON reply is short
