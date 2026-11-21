"""Generator agent (CLAUDE.md §6.2): drafts a cited answer from the retrieved sources only."""

from __future__ import annotations

from ai_service.agents.citations import strip_thinking
from ai_service.config import Settings
from ai_service.llm.ollama_client import OllamaClient
from ai_service.prompts import load_prompt
from ai_service.retrieval.retriever import Source, format_context

GENERATOR_TEMPERATURE = 0.2  # §6.6


class Generator:
    def __init__(self, llm: OllamaClient, settings: Settings) -> None:
        self._llm = llm
        self._settings = settings
        self._system_prompt = load_prompt("generator")

    def generate(self, question: str, sources: list[Source]) -> str:
        """One draft answer. The caller decides whether it may be shown (guards, Verifier)."""
        user = f"Sources:\n\n{format_context(sources)}\n\nQuestion: {question}"
        reply = self._llm.chat(
            [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": user},
            ],
            temperature=GENERATOR_TEMPERATURE,
        )
        return strip_thinking(reply)
