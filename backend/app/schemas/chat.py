"""Chat request/response contracts (CLAUDE.md §9). ``ChatResponse`` is frozen across tracks."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

MAX_MESSAGE_CHARS = 2000


class ChatMessageRequest(BaseModel):
    session_id: UUID
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be blank")
        return value


class Citation(BaseModel):
    n: int
    filename: str
    page: int


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation]
    status: Literal["verified", "fallback"]
    emergency: bool
    disclaimer: str
