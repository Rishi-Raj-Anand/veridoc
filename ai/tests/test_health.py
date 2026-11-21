"""Internal health endpoint (CLAUDE.md §9; contract frozen in task 0.4)."""

from __future__ import annotations

import pytest

from ai_service.routers.health import listed_model_names, normalise_model_name
from tests.helpers import FakeQdrant, build_app, internal_client


async def get_health(app) -> dict:
    async with internal_client(app) as client:
        response = await client.get("/internal/health")
    assert response.status_code == 200
    return response.json()


def test_normalise_model_name_adds_implicit_latest_tag() -> None:
    assert normalise_model_name("bge-m3") == "bge-m3:latest"
    assert normalise_model_name("qwen3:8b") == "qwen3:8b"
    assert normalise_model_name("bge-m3:latest") == "bge-m3:latest"


def test_listed_model_names_handles_object_entries() -> None:
    class Entry:
        def __init__(self, model: str) -> None:
            self.model = model

    class Listed:
        def __init__(self) -> None:
            self.models = [Entry("qwen3:8b"), Entry("bge-m3:latest")]

    assert listed_model_names(Listed()) == {"qwen3:8b", "bge-m3:latest"}


def test_listed_model_names_handles_dict_entries() -> None:
    listed = {"models": [{"name": "qwen3:8b"}, {"model": "bge-m3:latest"}]}
    assert listed_model_names(listed) == {"qwen3:8b", "bge-m3:latest"}


async def test_health_ok_when_everything_is_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Config says ``bge-m3``; Ollama reports ``bge-m3:latest``. Must still match."""
    app = build_app(monkeypatch, models=["qwen3:8b", "bge-m3:latest"])

    body = await get_health(app)

    assert body["status"] == "ok"
    assert body["ollama"] == {"ok": True, "host": "http://127.0.0.1:11434", "error": None}
    assert body["models"] == {"llm": True, "embed": True, "missing": []}
    assert body["qdrant"]["ok"] is True
    assert body["qdrant"]["path"].endswith("vectors")


async def test_health_degraded_when_a_model_is_not_pulled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_app(monkeypatch, models=["qwen3:8b"])

    body = await get_health(app)

    assert body["status"] == "degraded"
    assert body["models"] == {"llm": True, "embed": False, "missing": ["bge-m3:latest"]}
    assert body["ollama"]["ok"] is True


async def test_health_degraded_when_ollama_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_app(monkeypatch, ollama_fails=True)

    body = await get_health(app)

    assert body["status"] == "degraded"
    assert body["ollama"]["ok"] is False
    assert "ConnectionError" in body["ollama"]["error"]
    assert body["models"] == {
        "llm": False,
        "embed": False,
        "missing": ["bge-m3:latest", "qwen3:8b"],
    }


async def test_health_degraded_when_qdrant_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_app(
        monkeypatch,
        models=["qwen3:8b", "bge-m3:latest"],
        qdrant=FakeQdrant(fails=True),
    )

    body = await get_health(app)

    assert body["status"] == "degraded"
    assert body["qdrant"]["ok"] is False
    assert "another instance" in body["qdrant"]["error"]


async def test_health_degraded_when_qdrant_never_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_app(monkeypatch, models=["qwen3:8b", "bge-m3:latest"], qdrant=None)

    body = await get_health(app)

    assert body["status"] == "degraded"
    assert body["qdrant"]["error"] == "local storage was not opened at startup"
