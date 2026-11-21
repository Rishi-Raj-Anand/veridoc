"""POST /internal/chat (CLAUDE.md §9)."""

from __future__ import annotations

import pytest

from ai_service.config import DISCLAIMER, FALLBACK_MESSAGE
from ai_service.ingestion.chunker import ChildChunk
from tests.helpers import FakeLlm, build_app, internal_client, verifier_json

SESSION = "22222222-2222-4222-8222-222222222222"
VECTOR = [1.0] + [0.0] * 1023


def body(message: str = "How is malaria prevented?", **overrides: object) -> dict:
    return {"session_id": SESSION, "user_id": 3, "message": message, **overrides}


def seeded_app(monkeypatch: pytest.MonkeyPatch, llm: FakeLlm):
    app = build_app(monkeypatch, llm=llm)
    app.state.vector_store.upsert_children(
        1, "disease.md", [ChildChunk("1:p00000", 0, 1, "Use bed nets.")], [VECTOR]
    )
    return app


async def test_a_grounded_turn_matches_the_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    app = seeded_app(monkeypatch, FakeLlm(query_vector=VECTOR, reply="Use bed nets [1]."))

    async with internal_client(app) as client:
        response = await client.post("/internal/chat", json=body())

    assert response.status_code == 200
    assert response.json() == {
        "answer": "Use bed nets [1].",
        "citations": [{"n": 1, "filename": "disease.md", "page": 1}],
        "status": "verified",
        "emergency": False,
        "disclaimer": DISCLAIMER,
    }


async def test_an_ungrounded_turn_returns_the_fixed_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = seeded_app(monkeypatch, FakeLlm(query_vector=VECTOR, reply="NOT_IN_DOCUMENTS"))

    async with internal_client(app) as client:
        response = await client.post("/internal/chat", json=body())

    payload = response.json()
    assert (payload["status"], payload["answer"], payload["citations"]) == (
        "fallback",
        FALLBACK_MESSAGE,
        [],
    )
    assert payload["disclaimer"] == DISCLAIMER


async def test_a_draft_the_verifier_keeps_rejecting_returns_the_fallback_over_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = FakeLlm(
        query_vector=VECTOR,
        reply="Use bed nets [1].",
        verifier_replies=[verifier_json(faithfulness=0.2, verdict="fail", unsupported=["x"])],
    )
    app = seeded_app(monkeypatch, llm)

    async with internal_client(app) as client:
        response = await client.post("/internal/chat", json=body())

    payload = response.json()
    assert response.status_code == 200
    assert (payload["status"], payload["answer"], payload["citations"]) == (
        "fallback",
        FALLBACK_MESSAGE,
        [],
    )
    assert len(llm.verifier_calls) == 3  # three audits, then the fixed text, never a draft


async def test_missing_internal_key_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_app(monkeypatch)

    async with internal_client(app, key=None) as client:
        response = await client.post("/internal/chat", json=body())

    assert response.status_code == 401


@pytest.mark.parametrize(
    "payload",
    [body(""), body("   "), body("x" * 2001), body(session_id="not-a-uuid"), {"user_id": 1}],
)
async def test_invalid_bodies_are_422(monkeypatch: pytest.MonkeyPatch, payload: dict) -> None:
    app = build_app(monkeypatch)

    async with internal_client(app) as client:
        response = await client.post("/internal/chat", json=payload)

    assert response.status_code == 422


async def test_ollama_down_is_503_with_its_message(monkeypatch: pytest.MonkeyPatch) -> None:
    app = seeded_app(monkeypatch, FakeLlm(query_vector=VECTOR, embed_fails=True))

    async with internal_client(app) as client:
        response = await client.post("/internal/chat", json=body())

    assert response.status_code == 503
    assert "Ollama" in response.json()["detail"]
