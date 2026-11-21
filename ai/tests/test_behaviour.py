"""The admin-editable behaviour store and its internal endpoints (CLAUDE.md §6.9)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_service.agents.admin_text import admin_block
from ai_service.behaviour import (
    GUARDRAILS_MAX_CHARS,
    BehaviourSettings,
    BehaviourStore,
)
from ai_service.config import LOCKED_RULES
from tests.helpers import FakeLlm, build_app, intent_json, internal_client

SESSION = "33333333-3333-4333-8333-333333333333"


def test_a_missing_file_means_the_defaults(tmp_path: Path) -> None:
    store = BehaviourStore(tmp_path / "behaviour.json")

    assert store.get() == BehaviourSettings()
    assert store.stored().updated_at is None


def test_an_update_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "behaviour.json"
    BehaviourStore(path).update(BehaviourSettings(persona="Be brief."), "2026-11-21T10:00:00+00:00")

    reloaded = BehaviourStore(path)

    assert reloaded.get().persona == "Be brief."
    assert reloaded.stored().updated_at == "2026-11-21T10:00:00+00:00"


def test_a_corrupt_file_is_moved_aside_and_the_defaults_are_used(tmp_path: Path) -> None:
    path = tmp_path / "behaviour.json"
    path.write_text("{not json", encoding="utf-8")

    store = BehaviourStore(path)

    assert store.get() == BehaviourSettings()
    assert not path.exists()
    assert len(list(tmp_path.glob("behaviour.json.corrupt-*"))) == 1


def test_whitespace_is_stripped_and_an_empty_scope_is_rejected() -> None:
    assert BehaviourSettings(persona="  calm  ").persona == "calm"
    with pytest.raises(ValueError):
        BehaviourSettings(scope="   ")


def test_admin_block_is_empty_when_there_is_nothing_to_add() -> None:
    assert admin_block(BehaviourSettings(persona="", extra_guardrails="")) == ""


async def test_get_returns_settings_defaults_and_the_locked_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_app(monkeypatch)

    async with internal_client(app) as client:
        response = await client.get("/internal/behaviour")

    assert response.status_code == 200
    data = response.json()
    assert data["settings"] == data["defaults"] == BehaviourSettings().model_dump()
    assert data["locked_rules"] == list(LOCKED_RULES)
    assert data["updated_at"] is None


async def test_put_saves_and_the_next_chat_message_uses_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = FakeLlm(classify_replies=[intent_json("out_of_scope")])
    app = build_app(monkeypatch, llm=llm)
    update = {**BehaviourSettings().model_dump(), "out_of_scope_message": "Health only, sorry."}

    async with internal_client(app) as client:
        saved = await client.put("/internal/behaviour", json=update)
        chat = await client.post(
            "/internal/chat",
            json={"session_id": SESSION, "user_id": 1, "message": "plan my holiday"},
        )

    assert saved.status_code == 200
    assert saved.json()["settings"]["out_of_scope_message"] == "Health only, sorry."
    assert saved.json()["updated_at"] is not None
    assert chat.json()["status"] == "out_of_scope"
    assert chat.json()["answer"] == "Health only, sorry."
    assert chat.json()["citations"] == []


async def test_put_rejects_over_long_text_and_unknown_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_app(monkeypatch)
    base = BehaviourSettings().model_dump()

    async with internal_client(app) as client:
        too_long = await client.put(
            "/internal/behaviour",
            json={**base, "extra_guardrails": "x" * (GUARDRAILS_MAX_CHARS + 1)},
        )
        unknown = await client.put("/internal/behaviour", json={**base, "locked_rules": []})

    assert (too_long.status_code, unknown.status_code) == (422, 422)
    assert app.state.behaviour_store.get() == BehaviourSettings()


async def test_the_endpoints_require_the_internal_key(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_app(monkeypatch)

    async with internal_client(app, key=None) as client:
        get = await client.get("/internal/behaviour")
        put = await client.put("/internal/behaviour", json=BehaviourSettings().model_dump())

    assert (get.status_code, put.status_code) == (401, 401)
