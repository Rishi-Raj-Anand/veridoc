"""Verifier agent (CLAUDE.md §6.5): audits a draft against the retrieved context only.

The model returns strict JSON (Ollama structured output, ``temperature=0``). Whether the
draft *passes* is decided here in code from the thresholds; the model's own ``verdict`` is
necessary but never sufficient.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from ai_service.agents.citations import strip_thinking
from ai_service.config import Settings
from ai_service.llm.ollama_client import OllamaClient
from ai_service.prompts import load_prompt
from ai_service.retrieval.retriever import Source, format_context

logger = logging.getLogger(__name__)

VERIFIER_TEMPERATURE = 0.0  # §6.6
PARSE_ATTEMPTS = 2  # the first call plus one retry (§6.5)


class VerifierOutput(BaseModel):
    """The §6.5 schema, sent to Ollama as ``format=`` and used to validate the reply."""

    faithfulness: float = Field(ge=0.0, le=1.0)
    relevance: float = Field(ge=0.0, le=1.0)
    unsupported_claims: list[str]
    missing_info: str | None
    verdict: Literal["pass", "fail"]


def passes(output: VerifierOutput, settings: Settings) -> bool:
    """The §6.5 pass rule. Every condition must hold; ``verdict`` alone is not trusted."""
    return (
        output.verdict == "pass"
        and output.faithfulness >= settings.faithfulness_threshold
        and output.relevance >= settings.relevance_threshold
        and not output.unsupported_claims
    )


@dataclass(frozen=True)
class VerifierResult:
    passed: bool
    faithfulness: float
    relevance: float
    unsupported_claims: tuple[str, ...]
    missing_info: str | None
    parse_failed: bool = False

    @classmethod
    def parse_failure(cls) -> VerifierResult:
        """The reply was never valid JSON for the schema: treat the draft as failed."""
        return cls(False, 0.0, 0.0, (), None, parse_failed=True)


def _normalise(output: VerifierOutput) -> VerifierOutput:
    claims = [c.strip() for c in output.unsupported_claims if c.strip()]
    missing = (output.missing_info or "").strip() or None
    return output.model_copy(update={"unsupported_claims": claims, "missing_info": missing})


class Verifier:
    def __init__(self, llm: OllamaClient, settings: Settings) -> None:
        self._llm = llm
        self._settings = settings
        self._system_prompt = load_prompt("verifier")
        self._schema = VerifierOutput.model_json_schema()

    def verify(self, question: str, sources: list[Source], draft: str) -> VerifierResult:
        """Audit ``draft`` against ``sources`` only (never chat history, §6.2)."""
        user = (
            f"Sources:\n\n{format_context(sources)}\n\n"
            f"Question: {question}\n\nDraft answer:\n{draft}"
        )
        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user},
        ]
        for attempt in range(1, PARSE_ATTEMPTS + 1):
            reply = strip_thinking(
                self._llm.chat(messages, temperature=VERIFIER_TEMPERATURE, format=self._schema)
            )
            try:
                output = _normalise(VerifierOutput.model_validate_json(reply))
            except ValidationError:
                logger.warning(
                    "verifier reply was not valid (attempt %d/%d)", attempt, PARSE_ATTEMPTS
                )
                continue
            return VerifierResult(
                passed=passes(output, self._settings),
                faithfulness=output.faithfulness,
                relevance=output.relevance,
                unsupported_claims=tuple(output.unsupported_claims),
                missing_info=output.missing_info,
            )
        return VerifierResult.parse_failure()
