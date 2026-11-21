"""X-Internal-Key enforcement (CLAUDE.md §4, §9)."""

from __future__ import annotations

import pytest

from tests.helpers import INTERNAL_KEY, build_app, internal_client

MODELS = ["qwen3:8b", "bge-m3:latest"]


async def test_correct_key_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_app(monkeypatch, models=MODELS)
    async with internal_client(app) as client:
        response = await client.get("/internal/health")
    assert response.status_code == 200


async def test_missing_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Absent header must be 401, not FastAPI's default 422."""
    app = build_app(monkeypatch, models=MODELS)
    async with internal_client(app, key=None) as client:
        response = await client.get("/internal/health")
    assert response.status_code == 401
    assert response.json() == {"detail": "missing or invalid internal API key"}


async def test_wrong_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_app(monkeypatch, models=MODELS)
    async with internal_client(app, key="not-the-key") as client:
        response = await client.get("/internal/health")
    assert response.status_code == 401


async def test_key_comparison_is_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    """No trimming, no prefix matching."""
    app = build_app(monkeypatch, models=MODELS)
    for key in (f" {INTERNAL_KEY}", f"{INTERNAL_KEY} ", INTERNAL_KEY[:-1], INTERNAL_KEY.upper()):
        async with internal_client(app, key=key) as client:
            response = await client.get("/internal/health")
        assert response.status_code == 401, f"accepted {key!r}"
