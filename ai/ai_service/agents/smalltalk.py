"""Small-talk replies (CLAUDE.md §6.9): greetings and chit-chat, never medical content."""

from __future__ import annotations

from ai_service.agents.admin_text import admin_block
from ai_service.agents.citations import strip_thinking
from ai_service.behaviour import BehaviourStore
from ai_service.config import Settings
from ai_service.llm.ollama_client import OllamaClient
from ai_service.prompts import load_prompt

SMALLTALK_TEMPERATURE = 0.3  # a little variety; there are no facts to get wrong
MAX_REPLY_CHARS = 600
DEFAULT_REPLY = (
    "Hello! I'm Doctor AI. Ask me a health or medical question and I'll answer it from my "
    "reference documents."
)


def clip(text: str, limit: int = MAX_REPLY_CHARS) -> str:
    """Cut an over-long reply at the last sentence end that fits."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    end = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    return head[: end + 1] if end > 0 else head.rstrip() + "…"


class SmallTalk:
    def __init__(self, llm: OllamaClient, settings: Settings, behaviour: BehaviourStore) -> None:
        self._llm = llm
        self._settings = settings
        self._behaviour = behaviour
        self._system_prompt = load_prompt("smalltalk")

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    def reply(self, message: str) -> str:
        system = self._system_prompt + admin_block(self._behaviour.get())
        text = strip_thinking(
            self._llm.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": message}],
                temperature=SMALLTALK_TEMPERATURE,
                purpose="smalltalk",
            )
        ).strip()
        return clip(text) if text else DEFAULT_REPLY
