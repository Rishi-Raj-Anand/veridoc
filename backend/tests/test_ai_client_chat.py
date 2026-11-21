"""``ai_client.send_chat`` (CLAUDE.md §9, §17 task 3.5)."""

from __future__ import annotations

from uuid import UUID

import httpx
import pytest

from app.services import ai_client
from app.services.ai_client import AiServiceError
from tests.test_ai_client_documents import mock_ai, settings

SESSION = UUID("33333333-3333-4333-8333-333333333333")
REPLY = {
    "answer": "Use bed nets [1].",
    "citations": [{"n": 1, "filename": "disease.md", "page": 1}],
    "status": "verified",
    "emergency": False,
    "disclaimer": "d",
}


async def test_send_chat_posts_the_contract_body_and_parses_the_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = mock_ai(monkeypatch, lambda _r: httpx.Response(200, json=REPLY))

    result = await ai_client.send_chat(settings(), SESSION, 5, "How is malaria prevented?")

    assert result.answer == "Use bed nets [1]."
    assert result.citations[0].filename == "disease.md"
    request = seen[0]
    assert (request.method, str(request.url)) == ("POST", "http://ai:8001/internal/chat")
    assert request.headers["X-Internal-Key"] == "shared-key"
    assert request.read() == (
        b'{"session_id":"33333333-3333-4333-8333-333333333333","user_id":5,'
        b'"message":"How is malaria prevented?"}'
    )


def test_the_chat_timeout_is_well_above_the_latency_budget() -> None:
    assert ai_client.CHAT_TIMEOUT_SECONDS >= 180


@pytest.mark.parametrize("status", [500, 503])
async def test_server_errors_become_ai_service_errors(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    mock_ai(monkeypatch, lambda _r: httpx.Response(status, json={"detail": "model not pulled"}))

    with pytest.raises(AiServiceError, match="model not pulled"):
        await ai_client.send_chat(settings(), SESSION, 5, "q")


async def test_wrong_key_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_ai(monkeypatch, lambda _r: httpx.Response(401, json={"detail": "nope"}))

    with pytest.raises(AiServiceError, match="wrong INTERNAL_API_KEY"):
        await ai_client.send_chat(settings(), SESSION, 5, "q")


async def test_unreachable_service_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    mock_ai(monkeypatch, refuse)

    with pytest.raises(AiServiceError, match="AI service unavailable"):
        await ai_client.send_chat(settings(), SESSION, 5, "q")


@pytest.mark.parametrize("body", [{"ok": True}, {**REPLY, "status": "maybe"}])
async def test_a_malformed_reply_is_rejected(monkeypatch: pytest.MonkeyPatch, body: dict) -> None:
    mock_ai(monkeypatch, lambda _r: httpx.Response(200, json=body))

    with pytest.raises(AiServiceError, match="unexpected chat response"):
        await ai_client.send_chat(settings(), SESSION, 5, "q")
