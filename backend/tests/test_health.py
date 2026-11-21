"""Health endpoint and AI-service client (CLAUDE.md §9; contract frozen in task 0.4)."""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.main import create_app, ensure_data_dirs
from app.routers import health as health_router
from app.schemas.health import AiServiceHealth
from app.services import ai_client
from app.services.ai_client import AiServiceError

HEALTHY_AI_BODY = {
    "status": "ok",
    "ollama": {"ok": True, "host": "http://127.0.0.1:11434"},
    "models": {"llm": True, "embed": True, "missing": []},
    "qdrant": {"ok": True, "path": "D:/Codebase/Projects/veridoc/data/vectors"},
}


async def get_health() -> httpx.Response:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/api/health")


def patch_fetch_health(monkeypatch: pytest.MonkeyPatch, result: object) -> None:
    """Patch the name the router imported, not the one ai_client defines."""

    async def fake_fetch_health(_settings: Settings) -> AiServiceHealth:
        if isinstance(result, Exception):
            raise result
        return AiServiceHealth.model_validate(result)

    monkeypatch.setattr(health_router, "fetch_health", fake_fetch_health)


async def test_health_ok_when_ai_service_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_fetch_health(monkeypatch, HEALTHY_AI_BODY)

    response = await get_health()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["backend"] == "ok"
    assert body["error"] is None
    assert body["ai_service"]["models"] == {"llm": True, "embed": True, "missing": []}
    assert body["ai_service"]["qdrant"]["ok"] is True


async def test_health_degraded_when_ai_service_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Down dependency must still yield 200, so the caller learns *what* is broken."""
    patch_fetch_health(monkeypatch, AiServiceError("AI service unreachable: ConnectError"))

    response = await get_health()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["ai_service"] is None
    assert "unreachable" in body["error"]


async def test_health_degraded_when_a_model_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_fetch_health(
        monkeypatch,
        HEALTHY_AI_BODY
        | {
            "status": "degraded",
            "models": {"llm": True, "embed": False, "missing": ["bge-m3:latest"]},
        },
    )

    response = await get_health()

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "degraded"
    assert body["ai_service"]["models"]["missing"] == ["bge-m3:latest"]


def mock_ai_transport(monkeypatch: pytest.MonkeyPatch, response: httpx.Response) -> None:
    """Make ``ai_client``'s internally created AsyncClient use a mock transport."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Internal-Key"] == "shared-key"
        return response

    class MockedClient(httpx.AsyncClient):
        def __init__(self, **kwargs: object) -> None:
            kwargs.pop("timeout", None)
            transport = httpx.MockTransport(handler)
            super().__init__(transport=transport, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(ai_client.httpx, "AsyncClient", MockedClient)


def internal_settings() -> Settings:
    return Settings(_env_file=None, internal_api_key="shared-key")


async def test_fetch_health_sends_internal_key_and_parses_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_ai_transport(monkeypatch, httpx.Response(200, json=HEALTHY_AI_BODY))

    result = await ai_client.fetch_health(internal_settings())

    assert result.status == "ok"
    assert result.ollama.host == "http://127.0.0.1:11434"


async def test_fetch_health_maps_401_to_a_key_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_ai_transport(monkeypatch, httpx.Response(401, json={"detail": "invalid internal key"}))

    with pytest.raises(AiServiceError, match="wrong INTERNAL_API_KEY"):
        await ai_client.fetch_health(internal_settings())


async def test_fetch_health_rejects_an_unexpected_body(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_ai_transport(monkeypatch, httpx.Response(200, json={"status": "ok"}))

    with pytest.raises(AiServiceError, match="unexpected health body"):
        await ai_client.fetch_health(internal_settings())


def test_ensure_data_dirs_creates_the_tree(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    settings = Settings(_env_file=None)

    ensure_data_dirs(settings)

    assert settings.data_dir.is_dir()
    assert settings.uploads_dir.is_dir()
