"""Shared HTTP error mapping for the internal routers (CLAUDE.md §9)."""

from __future__ import annotations

from fastapi import HTTPException, status

from ai_service.llm.ollama_client import OllamaError

ERROR_CODE_HEADER = "X-Error-Code"


def ollama_unavailable(exc: OllamaError) -> HTTPException:
    """503 with the friendly message as ``detail`` and the cause as ``X-Error-Code``.

    The body keeps the standard ``{"detail": "..."}`` shape; the header lets the backend tell
    "Ollama down", "model not pulled" and "timed out" apart without parsing the text.
    """
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=str(exc),
        headers={ERROR_CODE_HEADER: exc.code},
    )
