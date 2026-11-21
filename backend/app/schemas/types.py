"""Shared field types for API schemas."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator


def _as_utc(value: datetime) -> datetime:
    """SQLite drops the timezone, so times read back are naive. They are stored as UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


# Serialised with a trailing "Z", so the browser converts it to local time correctly.
UtcDatetime = Annotated[datetime, AfterValidator(_as_utc)]
