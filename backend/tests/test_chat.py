"""``POST /api/chat/message`` (CLAUDE.md §9, §6.7, §17 task 3.5)."""

from __future__ import annotations

from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.models import ChatSession, User
from app.schemas.chat import ChatResponse
from app.services import ai_client, auth_service
from app.services.ai_client import AiServiceError
from tests.helpers import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    USER_PASSWORD,
    bearer,
    build_client,
    register,
    token_for,
)

pytestmark = pytest.mark.usefixtures("db_tables")

URL = "/api/chat/message"
ANSWER = ChatResponse(
    answer="Use bed nets [1].",
    citations=[{"n": 1, "filename": "disease.md", "page": 1}],
    status="verified",
    emergency=False,
    disclaimer="not advice",
)


class FakeAi:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, int, str]] = []
        self.error: Exception | None = None

    async def send_chat(
        self, _settings: Settings, session_id: UUID, user_id: int, message: str
    ) -> ChatResponse:
        self.calls.append((session_id, user_id, message))
        if self.error is not None:
            raise self.error
        return ANSWER


@pytest.fixture
def fake_ai(monkeypatch: pytest.MonkeyPatch) -> FakeAi:
    fake = FakeAi()
    monkeypatch.setattr(ai_client, "send_chat", fake.send_chat)
    return fake


@pytest.fixture
def client(db: Session, settings: Settings, fake_ai: FakeAi) -> httpx.AsyncClient:
    auth_service.seed_admin(db, settings)
    return build_client(create_app())


async def signed_in(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    assert (await register(client, email)).status_code == 201
    return bearer(await token_for(client, email, USER_PASSWORD))


def payload(session_id: UUID | str | None = None, message: str = "How is malaria prevented?"):
    return {"session_id": str(session_id or uuid4()), "message": message}


async def test_anonymous_is_401(client: httpx.AsyncClient, fake_ai: FakeAi) -> None:
    async with client:
        response = await client.post(URL, json=payload())

    assert response.status_code == 401
    assert fake_ai.calls == []


async def test_the_ai_response_passes_through_unchanged(
    client: httpx.AsyncClient, fake_ai: FakeAi
) -> None:
    async with client:
        headers = await signed_in(client, "a@example.com")
        response = await client.post(
            URL, json=payload(message="  How is malaria prevented?  "), headers=headers
        )

    assert response.status_code == 200
    assert response.json() == ANSWER.model_dump()
    assert fake_ai.calls[0][2] == "How is malaria prevented?"


async def test_first_message_creates_the_session_owned_by_the_caller(
    client: httpx.AsyncClient, fake_ai: FakeAi, db: Session
) -> None:
    session_id = uuid4()
    async with client:
        headers = await signed_in(client, "a@example.com")
        response = await client.post(URL, json=payload(session_id), headers=headers)

    assert response.status_code == 200
    row = db.get(ChatSession, str(session_id))
    owner = db.query(User).filter_by(email="a@example.com").one()
    assert row is not None and row.user_id == owner.id
    assert fake_ai.calls[0][:2] == (session_id, owner.id)


async def test_the_same_user_can_continue_a_session(
    client: httpx.AsyncClient, fake_ai: FakeAi, db: Session
) -> None:
    session_id = uuid4()
    async with client:
        headers = await signed_in(client, "a@example.com")
        first = await client.post(URL, json=payload(session_id), headers=headers)
        second = await client.post(
            URL, json=payload(session_id, "and for children?"), headers=headers
        )

    assert (first.status_code, second.status_code) == (200, 200)
    assert db.query(ChatSession).count() == 1
    assert len(fake_ai.calls) == 2


async def test_another_users_session_is_403_and_never_reaches_the_ai(
    client: httpx.AsyncClient, fake_ai: FakeAi
) -> None:
    session_id = uuid4()
    async with client:
        alice = await signed_in(client, "alice@example.com")
        bob = await signed_in(client, "bob@example.com")
        assert (await client.post(URL, json=payload(session_id), headers=alice)).status_code == 200
        response = await client.post(URL, json=payload(session_id), headers=bob)

    assert response.status_code == 403
    assert len(fake_ai.calls) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"session_id": "not-a-uuid", "message": "hi"},
        {"session_id": str(uuid4()), "message": ""},
        {"session_id": str(uuid4()), "message": "   "},
        {"session_id": str(uuid4()), "message": "x" * 2001},
        {"message": "hi"},
    ],
)
async def test_invalid_bodies_are_422(
    client: httpx.AsyncClient, fake_ai: FakeAi, body: dict
) -> None:
    async with client:
        headers = await signed_in(client, "a@example.com")
        response = await client.post(URL, json=body, headers=headers)

    assert response.status_code == 422
    assert fake_ai.calls == []


async def test_ai_service_down_is_a_friendly_503(
    client: httpx.AsyncClient, fake_ai: FakeAi
) -> None:
    fake_ai.error = AiServiceError("AI service unavailable at http://127.0.0.1:8001")

    async with client:
        headers = await signed_in(client, "a@example.com")
        response = await client.post(URL, json=payload(), headers=headers)

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Doctor AI is unavailable right now. Please try again in a moment."
    )


async def test_the_admin_may_chat_too(client: httpx.AsyncClient, fake_ai: FakeAi) -> None:
    async with client:
        headers = bearer(await token_for(client, ADMIN_EMAIL, ADMIN_PASSWORD))
        response = await client.post(URL, json=payload(), headers=headers)

    assert response.status_code == 200
