"""Health endpoint (CLAUDE.md §9)."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.schemas.health import HealthResponse
from app.services.ai_client import AiServiceError, fetch_health

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Backend and AI service health")
async def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    """Report backend health together with the AI service's own health.

    This returns 200 with ``status="degraded"`` when the AI service is unavailable,
    rather than 503: a health check that fails along with its dependency cannot say
    *which* dependency failed. The §9 mapping of AI failures to 503 applies to chat
    requests, not here.
    """
    try:
        ai = await fetch_health(settings)
    except AiServiceError as exc:
        logger.warning("AI service health check failed: %s", exc)
        return HealthResponse(status="degraded", error=str(exc))
    return HealthResponse(status=ai.status, ai_service=ai)
