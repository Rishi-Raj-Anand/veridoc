"""Internal API key check (CLAUDE.md §4, §9).

The AI service has no user auth. It binds to 127.0.0.1 and trusts only requests
carrying the correct ``X-Internal-Key``.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from ai_service.config import Settings, get_settings

INTERNAL_KEY_HEADER = "X-Internal-Key"


async def require_internal_key(
    settings: Annotated[Settings, Depends(get_settings)],
    x_internal_key: Annotated[str | None, Header(alias=INTERNAL_KEY_HEADER)] = None,
) -> None:
    """Reject the request unless the shared internal key matches exactly.

    The header is declared optional so a missing key yields 401 rather than FastAPI's
    422, and the comparison is constant-time.
    """
    expected = settings.internal_api_key
    if x_internal_key is None or not secrets.compare_digest(x_internal_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid internal API key",
        )
