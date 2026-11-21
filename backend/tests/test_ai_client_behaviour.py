"""``ai_client.get_behaviour`` / ``update_behaviour`` (CLAUDE.md §6.9)."""

from __future__ import annotations

import json
from uuid import UUID

import httpx
import pytest

from app.schemas.behaviour import BehaviourSettings
from app.services import ai_client
from app.services.ai_client import AiServiceError
from tests.test_ai_client_documents import mock_ai, settings

BEHAVIOUR = {
    "persona": "Warm.",
    "scope": "Health.",
    "out_of_scope_message": "Sorry.",
    "extra_guardrails": "",
}
REPLY = {
    "settings": BEHAVIOUR,
    "defaults": BEHAVIOUR,
    "locked_rules": ["Grounded only."],
    "updated_at": "2026-11-21T10:00:00+00:00",
}


async def test_get_behaviour_calls_the_internal_route(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = mock_ai(monkeypatch, lambda _r: httpx.Response(200, json=REPLY))

    result = await ai_client.get_behaviour(settings())

    assert result.locked_rules == ["Grounded only."]
    assert (seen[0].method, str(seen[0].url)) == ("GET", "http://ai:8001/internal/behaviour")
    assert seen[0].headers["X-Internal-Key"] == "shared-key"


async def test_update_behaviour_puts_the_whole_body(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = mock_ai(monkeypatch, lambda _r: httpx.Response(200, json=REPLY))

    await ai_client.update_behaviour(settings(), BehaviourSettings(**BEHAVIOUR))

    assert seen[0].method == "PUT"
    assert json.loads(seen[0].read()) == BEHAVIOUR


async def test_an_unexpected_body_is_an_ai_service_error(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_ai(monkeypatch, lambda _r: httpx.Response(200, json={"nope": True}))

    with pytest.raises(AiServiceError):
        await ai_client.get_behaviour(settings())


@pytest.mark.parametrize("status", ["smalltalk", "out_of_scope"])
async def test_chat_accepts_the_intent_statuses(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    reply = {
        "answer": "Hello!",
        "citations": [],
        "status": status,
        "emergency": False,
        "disclaimer": "d",
    }
    mock_ai(monkeypatch, lambda _r: httpx.Response(200, json=reply))

    result = await ai_client.send_chat(
        settings(), UUID("33333333-3333-4333-8333-333333333333"), 1, "hi"
    )

    assert result.status == status
