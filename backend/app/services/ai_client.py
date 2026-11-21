"""HTTP client for the internal AI service (CLAUDE.md §4, §9).

The backend never imports AI code and never calls Ollama. It speaks HTTP to the AI
service and authenticates with the shared ``X-Internal-Key``.
"""

from __future__ import annotations

import logging

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.schemas.health import AiServiceHealth

logger = logging.getLogger(__name__)

HEALTH_TIMEOUT_SECONDS = 5.0


class AiServiceError(RuntimeError):
    """The AI service was unreachable, refused us, or sent an unexpected body."""


def internal_headers(settings: Settings) -> dict[str, str]:
    return {"X-Internal-Key": settings.internal_api_key}


def _url(settings: Settings, path: str) -> str:
    return f"{settings.ai_service_url.rstrip('/')}{path}"


async def fetch_health(settings: Settings) -> AiServiceHealth:
    """Read ``GET /internal/health`` from the AI service.

    Raises ``AiServiceError`` for any failure; the caller decides how to report it.
    """
    url = _url(settings, "/internal/health")
    try:
        async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers=internal_headers(settings))
            response.raise_for_status()
            return AiServiceHealth.model_validate(response.json())
    except httpx.HTTPStatusError as exc:
        detail = "wrong INTERNAL_API_KEY" if exc.response.status_code == 401 else "error response"
        raise AiServiceError(
            f"AI service returned HTTP {exc.response.status_code} ({detail})"
        ) from exc
    except httpx.HTTPError as exc:
        raise AiServiceError(f"AI service unreachable at {url}: {type(exc).__name__}") from exc
    except (ValidationError, ValueError) as exc:
        message = f"AI service sent an unexpected health body: {type(exc).__name__}"
        raise AiServiceError(message) from exc
