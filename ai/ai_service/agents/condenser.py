"""Condenser agent (CLAUDE.md §6.2 ``condense_query``).

Rewrites a follow-up into a standalone question using the recent chat history, so retrieval
does not depend on the conversation. The first message of a session passes through unchanged
without an LLM call. Only the condenser ever sees history: the Generator and Verifier work from
the standalone question and the retrieved sources alone.
"""

from __future__ import annotations

import logging
import re

from ai_service.agents.budget import trim_history
from ai_service.agents.citations import NOT_IN_DOCUMENTS, strip_thinking
from ai_service.config import Settings
from ai_service.llm.ollama_client import OllamaClient
from ai_service.prompts import load_prompt

logger = logging.getLogger(__name__)

CONDENSER_TEMPERATURE = 0.0  # §6.6
MAX_QUERY_CHARS = 2000  # the same cap as a chat message

_LABEL = re.compile(
    r"^\s*(standalone\s+question|rewritten\s+question|question)\s*:\s*", re.IGNORECASE
)
_QUOTES = "\"'`“”‘’"  # noqa: RUF001 - typographic quotes are the point


def clean_condensed(text: str) -> str:
    """Strip thinking, a leading label, surrounding quotes and extra whitespace."""
    text = _LABEL.sub("", strip_thinking(text))
    text = " ".join(text.split()).strip(_QUOTES).strip()
    return text


class Condenser:
    def __init__(self, llm: OllamaClient, settings: Settings) -> None:
        self._llm = llm
        self._settings = settings
        self._system_prompt = load_prompt("condense")

    def condense(self, history: list[dict[str, str]], message: str) -> str:
        """The standalone question, or ``message`` itself when there is nothing to resolve."""
        history = trim_history(
            history, self._settings.history_max_tokens, self._settings.history_max_messages
        )
        if not history:
            return message
        conversation = "\n".join(
            f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}" for m in history
        )
        user = f"Conversation:\n{conversation}\n\nLatest message: {message}"
        reply = self._llm.chat(
            [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": user},
            ],
            temperature=CONDENSER_TEMPERATURE,
        )
        standalone = clean_condensed(reply)
        if not standalone or len(standalone) > MAX_QUERY_CHARS or NOT_IN_DOCUMENTS in standalone:
            logger.warning("condenser returned an unusable rewrite; using the raw message")
            return message
        return standalone
