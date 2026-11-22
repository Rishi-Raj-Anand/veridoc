"""HTTP client for the internal AI service (CLAUDE.md §4, §9).

The backend never imports AI code and never calls Ollama. It speaks HTTP to the AI
service and authenticates with the shared ``X-Internal-Key``.
"""

from __future__ import annotations

import logging
from uuid import UUID

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.schemas.behaviour import BehaviourResponse, BehaviourSettings
from app.schemas.chat import ChatResponse
from app.schemas.document import IngestResult
from app.schemas.health import AiServiceHealth

logger = logging.getLogger(__name__)

# Above the AI service's 3 s Ollama check, so a hung Ollama reads "Ollama down", not
# "AI service unreachable".
HEALTH_TIMEOUT_SECONDS = 8.0
DELETE_TIMEOUT_SECONDS = 30.0
# A turn can load models and run up to three retrieval rounds (§6.8: 30-90 s); stay well above.
CHAT_TIMEOUT_SECONDS = 300.0
BEHAVIOUR_TIMEOUT_SECONDS = 10.0


ERROR_CODE_HEADER = "X-Error-Code"


class AiServiceError(RuntimeError):
    """The AI service was unreachable, refused us, or sent an unexpected body.

    ``code`` names the cause so callers can pick a message without parsing text:
    ``ai_unreachable`` and ``ai_timeout`` are decided here; ``ollama_unreachable``,
    ``model_missing`` and ``ollama_timeout`` come from the AI service's ``X-Error-Code``.
    """

    def __init__(self, message: str, code: str = "ai_error") -> None:
        super().__init__(message)
        self.code = code


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
    except httpx.ConnectTimeout as exc:
        raise AiServiceError(
            f"AI service unreachable at {url}: {type(exc).__name__}", "ai_unreachable"
        ) from exc
    except httpx.TimeoutException as exc:
        raise AiServiceError(
            f"AI service did not answer the health check within {HEALTH_TIMEOUT_SECONDS:.0f} s",
            "ai_timeout",
        ) from exc
    except httpx.HTTPError as exc:
        raise AiServiceError(
            f"AI service unreachable at {url}: {type(exc).__name__}", "ai_unreachable"
        ) from exc
    except (ValidationError, ValueError) as exc:
        message = f"AI service sent an unexpected health body: {type(exc).__name__}"
        raise AiServiceError(message) from exc


def _error_detail(response: httpx.Response) -> str | None:
    """The ``detail`` of a FastAPI error body, if there is one.

    A validation error's list of ``{"msg": ...}`` items is joined with "; " (the frontend's
    rule), so a 422 from the AI service never collapses to "unexpected error".
    """
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        return None
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        messages = [str(i["msg"]) for i in detail if isinstance(i, dict) and "msg" in i]
        return "; ".join(messages) or None
    return None


async def _call(
    settings: Settings,
    method: str,
    path: str,
    *,
    timeout_seconds: float,
    json: object | None = None,
    timeout_detail: str | None = None,
) -> httpx.Response:
    """Send one internal request. Failures become ``AiServiceError`` with a friendly message.

    When the AI service explains itself (``{"detail": ...}``) that text is surfaced as-is,
    e.g. "scanned or image-only PDF is not supported", so the admin sees the real reason.
    """
    url = _url(settings, path)
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.request(
                method, url, headers=internal_headers(settings), json=json
            )
    except httpx.ConnectTimeout as exc:  # never connected: the same as refused
        raise AiServiceError(
            f"AI service unavailable at {settings.ai_service_url} ({type(exc).__name__}) - "
            "is it running?",
            "ai_unreachable",
        ) from exc
    except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as exc:
        # The service is reachable but did not answer in time: saying "is it running?" here
        # would send the admin looking for the wrong problem.
        raise AiServiceError(
            timeout_detail or f"AI service did not answer within {timeout_seconds:.0f} seconds",
            "ai_timeout",
        ) from exc
    except httpx.HTTPError as exc:
        raise AiServiceError(
            f"AI service unavailable at {settings.ai_service_url} ({type(exc).__name__}) - "
            "is it running?",
            "ai_unreachable",
        ) from exc
    if response.status_code == 401:
        raise AiServiceError("AI service rejected the request (wrong INTERNAL_API_KEY)")
    if response.is_error:
        detail = _error_detail(response) or "unexpected error"
        raise AiServiceError(
            f"{detail} (AI service HTTP {response.status_code})"
            if response.status_code >= 500
            else detail,
            response.headers.get(ERROR_CODE_HEADER, "ai_error"),
        )
    return response


def ingest_timeout_message(minutes: int) -> str:
    return (
        f"Indexing did not finish within {minutes} minutes, so VeriDoc stopped waiting. "
        "The file is probably too large: delete this entry (that also clears anything indexed "
        "in the background), then upload it in smaller parts, or raise INGEST_TIMEOUT_MINUTES "
        "in backend/.env."
    )


async def ingest_document(
    settings: Settings, doc_id: int, file_path: str, filename: str
) -> IngestResult:
    """Ask the AI service to parse, chunk, embed and index one uploaded file (§6.1)."""
    minutes = settings.ingest_timeout_minutes
    response = await _call(
        settings,
        "POST",
        "/internal/ingest",
        timeout_seconds=minutes * 60.0,
        json={"doc_id": doc_id, "file_path": file_path, "filename": filename},
        timeout_detail=ingest_timeout_message(minutes),
    )
    try:
        return IngestResult.model_validate(response.json())
    except (ValidationError, ValueError) as exc:
        raise AiServiceError("AI service sent an unexpected ingest response") from exc


async def delete_document_index(settings: Settings, doc_id: int) -> None:
    """Remove a document's vectors and parent chunks (idempotent on the AI side)."""
    await _call(
        settings, "DELETE", f"/internal/documents/{doc_id}", timeout_seconds=DELETE_TIMEOUT_SECONDS
    )


async def send_chat(
    settings: Settings, session_id: UUID, user_id: int, message: str
) -> ChatResponse:
    """Run one chat turn on the AI service and return its ``ChatResponse`` (§9)."""
    response = await _call(
        settings,
        "POST",
        "/internal/chat",
        timeout_seconds=CHAT_TIMEOUT_SECONDS,
        json={"session_id": str(session_id), "user_id": user_id, "message": message},
    )
    try:
        return ChatResponse.model_validate(response.json())
    except (ValidationError, ValueError) as exc:
        raise AiServiceError("AI service sent an unexpected chat response") from exc


async def get_behaviour(settings: Settings) -> BehaviourResponse:
    """The assistant's current behaviour, its defaults and the locked §7 rules (§6.9)."""
    response = await _call(
        settings, "GET", "/internal/behaviour", timeout_seconds=BEHAVIOUR_TIMEOUT_SECONDS
    )
    return _parse_behaviour(response)


async def update_behaviour(settings: Settings, body: BehaviourSettings) -> BehaviourResponse:
    """Replace the assistant's behaviour; it applies from the next chat message (§6.9)."""
    response = await _call(
        settings,
        "PUT",
        "/internal/behaviour",
        timeout_seconds=BEHAVIOUR_TIMEOUT_SECONDS,
        json=body.model_dump(),
    )
    return _parse_behaviour(response)


def _parse_behaviour(response: httpx.Response) -> BehaviourResponse:
    try:
        return BehaviourResponse.model_validate(response.json())
    except (ValidationError, ValueError) as exc:
        raise AiServiceError("AI service sent an unexpected behaviour response") from exc
