"""Role guards (CLAUDE.md §17 task 1.4)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session

from app.config import Settings
from app.deps import AdminUser
from app.main import create_app
from app.models import User
from app.services import auth_service
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


def app_with_admin_route() -> FastAPI:
    """A throwaway admin-guarded route, since the real ones arrive in Phase 2."""
    app = create_app()

    @app.get("/api/_admin-only")
    def admin_only(user: AdminUser) -> dict[str, str]:
        return {"email": user.email}

    return app


async def test_a_regular_user_gets_403_on_an_admin_route() -> None:
    async with build_client(app_with_admin_route()) as client:
        await register(client)
        token = await token_for(client, "user@example.com", USER_PASSWORD)
        response = await client.get("/api/_admin-only", headers=bearer(token))

    assert response.status_code == 403


async def test_the_admin_passes_the_admin_guard(db: Session, settings: Settings) -> None:
    auth_service.seed_admin(db, settings)
    async with build_client(app_with_admin_route()) as client:
        token = await token_for(client, ADMIN_EMAIL, ADMIN_PASSWORD)
        response = await client.get("/api/_admin-only", headers=bearer(token))

    assert response.status_code == 200
    assert response.json() == {"email": ADMIN_EMAIL}


async def test_an_admin_route_without_a_token_is_401() -> None:
    async with build_client(app_with_admin_route()) as client:
        response = await client.get("/api/_admin-only")

    assert response.status_code == 401


async def test_a_token_for_a_deleted_user_is_401(db: Session) -> None:
    async with build_client() as client:
        await register(client)
        token = await token_for(client, "user@example.com", USER_PASSWORD)
        db.delete(db.query(User).filter_by(email="user@example.com").one())
        db.commit()
        response = await client.get("/api/auth/me", headers=bearer(token))

    assert response.status_code == 401


async def test_the_role_comes_from_the_database_not_the_token(
    db: Session, settings: Settings
) -> None:
    """A token minted while the user was an admin must stop working as admin."""
    auth_service.seed_admin(db, settings)
    async with build_client(app_with_admin_route()) as client:
        token = await token_for(client, ADMIN_EMAIL, ADMIN_PASSWORD)
        admin = db.query(User).filter_by(role="admin").one()
        admin.role = "user"
        db.commit()
        response = await client.get("/api/_admin-only", headers=bearer(token))

    assert response.status_code == 403
