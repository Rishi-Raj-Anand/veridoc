"""Intent routing (CLAUDE.md §6.9): is this message medical, small talk, or off-topic?

``safety_check`` runs first: a red-flag message is always ``medical`` and never reaches this
module. Pure greetings are recognised by a regex with no LLM call. Everything else is one
structured-output call at temperature 0; when in doubt, or when the reply never validates,
the answer is ``medical``, because the RAG loop is the safe path (at worst it falls back).
"""

from __future__ import annotations

import logging
import re

from pydantic import BaseModel, ValidationError

from ai_service.agents.citations import strip_thinking
from ai_service.config import Settings
from ai_service.contracts import Intent
from ai_service.llm.ollama_client import OllamaClient
from ai_service.prompts import load_prompt

logger = logging.getLogger(__name__)

CLASSIFIER_TEMPERATURE = 0.0  # §6.6
PARSE_ATTEMPTS = 2  # the first call plus one retry, as for the Verifier
CONTEXT_MESSAGES = 2  # the previous exchange, so "what about for kids?" stays medical
CONTEXT_CHARS = 300  # per earlier message: enough for the subject, cheap on tokens
GREETING_MAX_CHARS = 60

_GREETING = re.compile(
    r"^(?:(?:hi+|hello+|hey+|hiya|namaste|yo|"
    r"good (?:morning|afternoon|evening|night)|"
    r"thanks?(?: a lot)?|thank you(?: so much| very much)?|thx|ty|"
    r"ok(?:ay)?|cool|great|nice|bye|goodbye|see you|"
    r"how are you(?: doing)?|how s it going|who are you|what can you do)"
    r"(?: (?:there|doc|doctor|doctor ai|again))?\s*)+$"
)


def is_greeting(message: str) -> bool:
    """A short message made only of greeting / thanks / goodbye phrases."""
    if len(message) > GREETING_MAX_CHARS:
        return False
    normalised = " ".join(re.sub(r"[\W_]+", " ", message.lower()).split())
    return bool(normalised) and _GREETING.fullmatch(normalised) is not None


class IntentOutput(BaseModel):
    intent: Intent


class IntentClassifier:
    def __init__(self, llm: OllamaClient, settings: Settings) -> None:
        self._llm = llm
        self._settings = settings
        self._system_prompt = load_prompt("classify")
        self._schema = IntentOutput.model_json_schema()

    def classify(self, message: str, history: list[dict[str, str]], scope: str) -> Intent:
        recent = history[-CONTEXT_MESSAGES:]
        conversation = "\n".join(
            f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content'][:CONTEXT_CHARS]}"
            for m in recent
        )
        user = (
            f"In-scope topics: {scope}\n\n"
            f"Recent conversation:\n{conversation or '(none)'}\n\n"
            f"Message to classify: {message}"
        )
        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user},
        ]
        for attempt in range(1, PARSE_ATTEMPTS + 1):
            reply = strip_thinking(
                self._llm.chat(
                    messages,
                    temperature=CLASSIFIER_TEMPERATURE,
                    format=self._schema,
                    purpose="classify",
                )
            )
            try:
                return IntentOutput.model_validate_json(reply).intent
            except ValidationError:
                logger.warning(
                    "classifier reply was not valid (attempt %d/%d)", attempt, PARSE_ATTEMPTS
                )
        logger.warning("classifier never returned a valid intent; treating it as medical")
        return "medical"
