"""Document request/response contracts (CLAUDE.md §8, §9)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.types import UtcDatetime

DocumentStatus = Literal["pending", "ready", "failed"]


class DocumentResponse(BaseModel):
    """What the admin UI sees. Deliberately omits ``stored_path`` and ``sha256``."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    size_bytes: int
    status: DocumentStatus
    chunk_count: int
    error: str | None
    created_at: UtcDatetime


class IngestResult(BaseModel):
    """Body of the AI service's ``POST /internal/ingest`` (§9)."""

    model_config = ConfigDict(extra="allow")

    chunk_count: int
