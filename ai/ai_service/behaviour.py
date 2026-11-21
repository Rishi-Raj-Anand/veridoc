"""Admin-editable assistant behaviour (CLAUDE.md §6.9).

The admin can tune tone, what counts as in scope, the refusal text and extra guardrails.
Stored in ``data/behaviour.json`` and applied from the next message, no restart needed.
These texts can only *add* restrictions: the §7 safety rules stay in the prompts, come first,
and are listed read-only as ``LOCKED_RULES``. Every field is capped so the admin text can never
eat the §6.4 prompt budget.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_service.storage import atomic_write_text

logger = logging.getLogger(__name__)

PERSONA_MAX_CHARS = 600
SCOPE_MAX_CHARS = 600
OUT_OF_SCOPE_MAX_CHARS = 400
GUARDRAILS_MAX_CHARS = 1500

DEFAULT_PERSONA = "Warm, calm and clear. Use plain language that a patient can follow."
DEFAULT_SCOPE = (
    "Medical and health questions: symptoms, conditions and diseases, medicines and remedies, "
    "first aid, prevention, and healthy habits."
)
DEFAULT_OUT_OF_SCOPE_MESSAGE = (
    "Sorry, I can't help with that. I'm Doctor AI, and I can only answer medical and health "
    "questions from my reference documents."
)


class BehaviourSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    persona: str = Field(default=DEFAULT_PERSONA, max_length=PERSONA_MAX_CHARS)
    scope: str = Field(default=DEFAULT_SCOPE, min_length=1, max_length=SCOPE_MAX_CHARS)
    out_of_scope_message: str = Field(
        default=DEFAULT_OUT_OF_SCOPE_MESSAGE, min_length=1, max_length=OUT_OF_SCOPE_MAX_CHARS
    )
    extra_guardrails: str = Field(default="", max_length=GUARDRAILS_MAX_CHARS)


class StoredBehaviour(BaseModel):
    settings: BehaviourSettings = Field(default_factory=BehaviourSettings)
    updated_at: str | None = None


class BehaviourStore:
    """Thread-safe holder of the current behaviour, backed by one JSON file."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._current = self._load()

    def _load(self) -> StoredBehaviour:
        if not self._path.exists():
            return StoredBehaviour()
        try:
            return StoredBehaviour.model_validate_json(self._path.read_text(encoding="utf-8"))
        except (ValidationError, ValueError, OSError) as exc:
            quarantine = self._path.with_name(f"{self._path.name}.corrupt-{int(time.time())}")
            logger.warning("behaviour file is unreadable (%s); using the defaults", exc)
            try:
                os.replace(self._path, quarantine)
            except OSError:
                logger.exception("could not quarantine %s", self._path.name)
            return StoredBehaviour()

    def get(self) -> BehaviourSettings:
        with self._lock:
            return self._current.settings

    def stored(self) -> StoredBehaviour:
        with self._lock:
            return self._current

    def update(self, settings: BehaviourSettings, updated_at: str) -> StoredBehaviour:
        stored = StoredBehaviour(settings=settings, updated_at=updated_at)
        with self._lock:
            atomic_write_text(self._path, stored.model_dump_json(indent=2))
            self._current = stored
        logger.info("assistant behaviour updated")
        return stored
