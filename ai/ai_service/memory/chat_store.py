"""Chat transcripts: ``data/chats/<session_id>.json`` (CLAUDE.md §6.7).

One file per session. Each exchange adds two entries (the user's message, then the
assistant's answer with its evaluation metadata). Files are written atomically: a temp file
in the same directory, then ``os.replace``. Verifier scores and iteration counts are kept for
evaluation and are never shown to users.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError

from ai_service.config import EMERGENCY_NOTICE
from ai_service.contracts import ChatStatus, Intent
from ai_service.storage import atomic_write_text

logger = logging.getLogger(__name__)


class SessionOwnershipError(Exception):
    """The transcript belongs to a different user than the one asking."""


class TurnEntry(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    timestamp: str
    # Assistant entries only.
    standalone_query: str | None = None
    status: ChatStatus | None = None
    intent: Intent | None = None
    emergency: bool | None = None
    iterations: int | None = None
    citations: list[dict[str, Any]] | None = None
    verifier_scores: list[dict[str, Any]] | None = None
    llm_calls: list[dict[str, Any]] | None = None  # purpose, tokens, seconds (8.4)


class Transcript(BaseModel):
    session_id: str
    user_id: int
    created_at: str
    turns: list[TurnEntry] = Field(default_factory=list)


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def strip_notice(content: str) -> str:
    """Remove the emergency notice prefix, so history shows only what was answered."""
    if content.startswith(EMERGENCY_NOTICE):
        return content[len(EMERGENCY_NOTICE) :].lstrip()
    return content


class ChatStore:
    def __init__(self, chats_dir: Path) -> None:
        self._dir = chats_dir

    def _path(self, session_id: str) -> Path:
        # Parsing as a UUID guarantees a safe filename (no separators, no traversal).
        return self._dir / f"{UUID(str(session_id))}.json"

    def load(self, session_id: str, user_id: int) -> Transcript | None:
        """The session's transcript, or ``None`` if it is new (or its file was unusable)."""
        path = self._path(session_id)
        if not path.exists():
            return None
        try:
            transcript = Transcript.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValidationError, ValueError, OSError) as exc:
            quarantine = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
            logger.warning("transcript %s is unreadable (%s); moving it aside", path.name, exc)
            try:
                os.replace(path, quarantine)
            except OSError:
                logger.exception("could not quarantine %s", path.name)
            return None
        if transcript.user_id != user_id:
            raise SessionOwnershipError(f"session {session_id} belongs to another user")
        return transcript

    @staticmethod
    def history(transcript: Transcript | None, max_messages: int) -> list[dict[str, str]]:
        """The last ``max_messages`` entries as plain ``{role, content}`` (notice stripped)."""
        if transcript is None or max_messages <= 0:
            return []
        return [
            {"role": t.role, "content": strip_notice(t.content)}
            for t in transcript.turns[-max_messages:]
        ]

    def append_exchange(
        self,
        session_id: str,
        user_id: int,
        user_entry: TurnEntry,
        assistant_entry: TurnEntry,
        transcript: Transcript | None = None,
    ) -> None:
        """Add one exchange and save. Pass the transcript already loaded this turn, if any."""
        if transcript is None:
            transcript = self.load(session_id, user_id)
        if transcript is None:
            transcript = Transcript(
                session_id=str(UUID(str(session_id))), user_id=user_id, created_at=now_iso()
            )
        transcript.turns.extend([user_entry, assistant_entry])
        self._write(self._path(session_id), transcript)

    def _write(self, path: Path, transcript: Transcript) -> None:
        payload = json.dumps(transcript.model_dump(mode="json"), ensure_ascii=False, indent=2)
        atomic_write_text(path, payload)
