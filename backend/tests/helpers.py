"""Shared test helpers for the backend."""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from app.main import create_app

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "test-admin-password"
USER_PASSWORD = "a-good-password"


def build_client(app: FastAPI | None = None) -> httpx.AsyncClient:
    """An HTTP client over the ASGI app. ``ASGITransport`` does not run lifespan."""
    transport = httpx.ASGITransport(app=app or create_app())
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def register(
    client: httpx.AsyncClient, email: str = "user@example.com", password: str = USER_PASSWORD
) -> httpx.Response:
    return await client.post("/api/auth/register", json={"email": email, "password": password})


async def login(
    client: httpx.AsyncClient, email: str = "user@example.com", password: str = USER_PASSWORD
) -> httpx.Response:
    return await client.post("/api/auth/login", json={"email": email, "password": password})


async def token_for(client: httpx.AsyncClient, email: str, password: str) -> str:
    response = await login(client, email, password)
    assert response.status_code == 200, response.text
    return response.json()["access_token"]
