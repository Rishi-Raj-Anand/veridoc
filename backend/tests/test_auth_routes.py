"""Register / login / me (CLAUDE.md §9, §17 task 1.3)."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import User
from app.services import auth_service
from app.services.auth_service import ALGORITHM
from tests.helpers import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    USER_PASSWORD,
    bearer,
    build_client,
    login,
    register,
    token_for,
)

pytestmark = pytest.mark.usefixtures("db_tables")


async def test_register_creates_a_regular_user() -> None:
    async with build_client() as client:
        response = await register(client)

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "user@example.com"
    assert body["role"] == "user"
    assert "password" not in body
    assert "password_hash" not in body


async def test_register_stores_a_hash_not_the_password(db: Session) -> None:
    async with build_client() as client:
        await register(client)

    user = db.scalar(select(User).where(User.email == "user@example.com"))
    assert user is not None
    assert user.password_hash != USER_PASSWORD
    assert auth_service.verify_password(user.password_hash, USER_PASSWORD)


async def test_duplicate_email_is_a_conflict() -> None:
    async with build_client() as client:
        await register(client)
        response = await register(client)

    assert response.status_code == 409


async def test_duplicate_email_differing_only_in_case_is_a_conflict() -> None:
    async with build_client() as client:
        await register(client, email="user@example.com")
        response = await register(client, email="USER@Example.com")

    assert response.status_code == 409


async def test_the_admin_email_cannot_be_registered() -> None:
    async with build_client() as client:
        response = await register(client, email=ADMIN_EMAIL)

    assert response.status_code == 409


async def test_short_password_is_rejected() -> None:
    async with build_client() as client:
        response = await register(client, password="1234567")

    assert response.status_code == 422


async def test_overlong_password_is_rejected() -> None:
    async with build_client() as client:
        response = await register(client, password="x" * 129)

    assert response.status_code == 422


async def test_invalid_email_is_rejected() -> None:
    async with build_client() as client:
        response = await register(client, email="not-an-email")

    assert response.status_code == 422


async def test_registration_cannot_choose_a_role(db: Session) -> None:
    async with build_client() as client:
        response = await client.post(
            "/api/auth/register",
            json={"email": "evil@example.com", "password": USER_PASSWORD, "role": "admin"},
        )

    assert response.status_code == 422
    assert db.scalar(select(User).where(User.email == "evil@example.com")) is None


async def test_login_returns_a_token_and_role() -> None:
    async with build_client() as client:
        await register(client)
        response = await login(client)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["role"] == "user"
    assert body["access_token"]


async def test_login_is_case_insensitive_on_email() -> None:
    async with build_client() as client:
        await register(client)
        response = await login(client, email="USER@example.com")

    assert response.status_code == 200


async def test_wrong_password_and_unknown_email_look_identical() -> None:
    async with build_client() as client:
        await register(client)
        wrong_password = await login(client, password="not-the-password")
        unknown_email = await login(client, email="nobody@example.com")

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


async def test_me_returns_the_current_user() -> None:
    async with build_client() as client:
        await register(client)
        token = await token_for(client, "user@example.com", USER_PASSWORD)
        response = await client.get("/api/auth/me", headers=bearer(token))

    assert response.status_code == 200
    assert response.json()["email"] == "user@example.com"


async def test_me_without_a_token_is_401() -> None:
    async with build_client() as client:
        response = await client.get("/api/auth/me")

    assert response.status_code == 401


@pytest.mark.parametrize("header", ["Bearer garbage", "Basic abc", "Bearer"])
async def test_me_with_a_malformed_token_is_401(header: str) -> None:
    async with build_client() as client:
        response = await client.get("/api/auth/me", headers={"Authorization": header})

    assert response.status_code == 401


async def test_me_with_an_expired_token_is_401(db: Session) -> None:
    async with build_client() as client:
        await register(client)
        user = db.scalar(select(User).where(User.email == "user@example.com"))
        assert user is not None
        expired = auth_service.create_access_token(
            Settings(_env_file=None, jwt_expire_minutes=-1), user
        )
        response = await client.get("/api/auth/me", headers=bearer(expired))

    assert response.status_code == 401
    assert "expired" in response.json()["detail"]


async def test_the_seeded_admin_can_log_in_as_admin(db: Session, settings: Settings) -> None:
    auth_service.seed_admin(db, settings)
    async with build_client() as client:
        response = await login(client, ADMIN_EMAIL, ADMIN_PASSWORD)

    assert response.status_code == 200
    assert response.json()["role"] == "admin"


def test_algorithm_is_pinned() -> None:
    assert ALGORITHM == "HS256"
