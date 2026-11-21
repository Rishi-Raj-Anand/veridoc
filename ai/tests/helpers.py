"""Shared test helpers for the AI service."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from ai_service.main import create_app
from ai_service.routers import health as health_router

INTERNAL_KEY = "test-internal-key"


class FakeQdrant:
    """Stand-in for the embedded QdrantClient that lifespan opens."""

    def __init__(self, *, fails: bool = False) -> None:
        self.fails = fails
        self.closed = False

    def get_collections(self) -> object:
        if self.fails:
            raise RuntimeError("storage folder is already accessed by another instance")
        return object()

    def close(self) -> None:
        self.closed = True


class FakeOllamaClient:
    """Stand-in for ``ollama.Client``."""

    def __init__(self, models: list[str], *, fails: bool = False) -> None:
        self.models = models
        self.fails = fails

    def list(self) -> dict[str, Any]:
        if self.fails:
            raise ConnectionError("connection refused")
        return {"models": [{"model": name} for name in self.models]}


def build_app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    models: list[str] | None = None,
    ollama_fails: bool = False,
    qdrant: FakeQdrant | str | None = "default",
) -> FastAPI:
    """Create the app with Ollama and Qdrant replaced by fakes.

    ``ASGITransport`` does not run lifespan, so ``app.state.qdrant`` is set here by
    hand. Pass ``qdrant=None`` to simulate storage that never opened.
    """

    def client_factory(host: str) -> FakeOllamaClient:
        return FakeOllamaClient(models or [], fails=ollama_fails)

    monkeypatch.setattr(health_router.ollama, "Client", client_factory)
    app = create_app()
    app.state.qdrant = FakeQdrant() if qdrant == "default" else qdrant
    return app


def internal_client(app: FastAPI, *, key: str | None = INTERNAL_KEY) -> httpx.AsyncClient:
    """An HTTP client for the app, sending the internal key unless ``key=None``."""
    headers = {"X-Internal-Key": key} if key is not None else {}
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=headers,
    )
