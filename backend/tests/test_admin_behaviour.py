"""``GET/PUT /api/admin/behaviour`` (CLAUDE.md §6.9, §9). Admin only; proxies the AI service."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.schemas.behaviour import GUARDRAILS_MAX_CHARS, BehaviourResponse, BehaviourSettings
from app.services import ai_client, auth_service
from app.services.ai_client import AiServiceError
from tests.helpers import ADMIN_EMAIL, ADMIN_PASSWORD, bearer, build_client, register, token_for

pytestmark = pytest.mark.usefixtures("db_tables")

URL = "/api/admin/behaviour"
SETTINGS = BehaviourSettings(
    persona="Warm.", scope="Health.", out_of_scope_message="Sorry.", extra_guardrails=""
)


class FakeAi:
    def __init__(self) -> None:
        self.current = SETTINGS
        self.saved: list[BehaviourSettings] = []
        self.error: Exception | None = None

    def _response(self) -> BehaviourResponse:
        return BehaviourResponse(
            settings=self.current, defaults=SETTINGS, locked_rules=["rule"], updated_at=None
        )

    async def get(self, _settings: Settings) -> BehaviourResponse:
        if self.error is not None:
            raise self.error
        return self._response()

    async def update(self, _settings: Settings, body: BehaviourSettings) -> BehaviourResponse:
        if self.error is not None:
            raise self.error
        self.saved.append(body)
        self.current = body
        return self._response()


@pytest.fixture
def fake_ai(monkeypatch: pytest.MonkeyPatch) -> FakeAi:
    fake = FakeAi()
    monkeypatch.setattr(ai_client, "get_behaviour", fake.get)
    monkeypatch.setattr(ai_client, "update_behaviour", fake.update)
    return fake


@pytest.fixture
def client(db: Session, settings: Settings, fake_ai: FakeAi) -> httpx.AsyncClient:
    auth_service.seed_admin(db, settings)
    return build_client(create_app())


async def admin_headers(client: httpx.AsyncClient) -> dict[str, str]:
    return bearer(await token_for(client, ADMIN_EMAIL, ADMIN_PASSWORD))


async def test_anonymous_is_401_and_a_user_is_403(client: httpx.AsyncClient) -> None:
    async with client:
        await register(client)
        user = bearer(await token_for(client, "user@example.com", "a-good-password"))
        anonymous = await client.get(URL)
        as_user_get = await client.get(URL, headers=user)
        as_user_put = await client.put(URL, headers=user, json=SETTINGS.model_dump())

    assert anonymous.status_code == 401
    assert (as_user_get.status_code, as_user_put.status_code) == (403, 403)


async def test_the_admin_reads_settings_defaults_and_locked_rules(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        response = await client.get(URL, headers=await admin_headers(client))

    assert response.status_code == 200
    assert response.json() == {
        "settings": SETTINGS.model_dump(),
        "defaults": SETTINGS.model_dump(),
        "locked_rules": ["rule"],
        "updated_at": None,
    }


async def test_the_admin_saves_and_whitespace_is_trimmed(
    client: httpx.AsyncClient, fake_ai: FakeAi
) -> None:
    body = {**SETTINGS.model_dump(), "persona": "  Be brief.  "}
    async with client:
        response = await client.put(URL, headers=await admin_headers(client), json=body)

    assert response.status_code == 200
    assert fake_ai.saved[0].persona == "Be brief."
    assert response.json()["settings"]["persona"] == "Be brief."


@pytest.mark.parametrize(
    "change",
    [
        {"extra_guardrails": "x" * (GUARDRAILS_MAX_CHARS + 1)},
        {"scope": "   "},
        {"locked_rules": []},
    ],
)
async def test_a_bad_body_is_422_and_never_reaches_the_ai_service(
    client: httpx.AsyncClient, fake_ai: FakeAi, change: dict[str, object]
) -> None:
    async with client:
        response = await client.put(
            URL, headers=await admin_headers(client), json={**SETTINGS.model_dump(), **change}
        )

    assert response.status_code == 422
    assert fake_ai.saved == []


async def test_an_unreachable_ai_service_is_a_friendly_503(
    client: httpx.AsyncClient, fake_ai: FakeAi
) -> None:
    fake_ai.error = AiServiceError("AI service unavailable at http://127.0.0.1:8001")
    async with client:
        headers = await admin_headers(client)
        read = await client.get(URL, headers=headers)
        save = await client.put(URL, headers=headers, json=SETTINGS.model_dump())

    assert (read.status_code, save.status_code) == (503, 503)
    assert "could not be loaded" in read.json()["detail"]
    assert "nothing was saved" in save.json()["detail"]
