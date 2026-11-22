"""Phase 8.1 error-handling sweep: each failure gives a specific, friendly message."""

from __future__ import annotations

import logging
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.main import create_app
from app.middleware import SERVER_ERROR
from app.models import Document
from app.routers import health as health_router
from app.routers.chat import AI_UNAVAILABLE, MODEL_OFFLINE, TOO_SLOW
from app.schemas.behaviour import BehaviourSettings
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
from tests.test_ai_client_documents import mock_ai, settings

ORIGIN = "http://localhost:5173"
BEHAVIOUR = BehaviourSettings(
    persona="p", scope="health", out_of_scope_message="no", extra_guardrails=""
)


def raising(exc: Exception):
    def handler(_request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


# --- ai_client: codes and messages --------------------------------------------------------


async def test_a_connect_timeout_counts_as_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_ai(monkeypatch, raising(httpx.ConnectTimeout("slow connect")))

    with pytest.raises(AiServiceError, match="is it running") as caught:
        await ai_client.send_chat(settings(), uuid4(), 1, "q")

    assert caught.value.code == "ai_unreachable"


async def test_a_refused_connection_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_ai(monkeypatch, raising(httpx.ConnectError("refused")))

    with pytest.raises(AiServiceError) as caught:
        await ai_client.send_chat(settings(), uuid4(), 1, "q")

    assert caught.value.code == "ai_unreachable"


async def test_a_read_timeout_is_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_ai(monkeypatch, raising(httpx.ReadTimeout("slow answer")))

    with pytest.raises(AiServiceError, match="did not answer within 300 seconds") as caught:
        await ai_client.send_chat(settings(), uuid4(), 1, "q")

    assert caught.value.code == "ai_timeout"


@pytest.mark.parametrize("code", ["ollama_unreachable", "model_missing", "ollama_timeout"])
async def test_the_ai_services_error_code_is_kept(
    monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    mock_ai(
        monkeypatch,
        lambda _r: httpx.Response(503, json={"detail": "why"}, headers={"X-Error-Code": code}),
    )

    with pytest.raises(AiServiceError, match=r"why \(AI service HTTP 503\)") as caught:
        await ai_client.send_chat(settings(), uuid4(), 1, "q")

    assert caught.value.code == code


async def test_a_validation_error_list_is_joined(monkeypatch: pytest.MonkeyPatch) -> None:
    detail = [{"loc": ["body", "message"], "msg": "too long"}, {"msg": "not blank"}]
    mock_ai(monkeypatch, lambda _r: httpx.Response(422, json={"detail": detail}))

    with pytest.raises(AiServiceError, match=r"^too long; not blank$"):
        await ai_client.send_chat(settings(), uuid4(), 1, "q")


async def test_an_error_without_a_detail_is_still_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_ai(monkeypatch, lambda _r: httpx.Response(418, text="teapot"))

    with pytest.raises(AiServiceError, match=r"^unexpected error$") as caught:
        await ai_client.send_chat(settings(), uuid4(), 1, "q")

    assert caught.value.code == "ai_error"


@pytest.mark.parametrize(
    ("error", "message", "code"),
    [
        (httpx.ReadTimeout("slow"), "did not answer within 10 seconds", "ai_timeout"),
        (httpx.ConnectError("refused"), "is it running", "ai_unreachable"),
    ],
)
async def test_behaviour_calls_map_failures_too(
    monkeypatch: pytest.MonkeyPatch, error: Exception, message: str, code: str
) -> None:
    mock_ai(monkeypatch, raising(error))

    with pytest.raises(AiServiceError, match=message) as caught:
        await ai_client.update_behaviour(settings(), BEHAVIOUR)

    assert caught.value.code == code


# --- health -------------------------------------------------------------------------------


async def test_a_slow_health_check_is_not_called_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_ai(monkeypatch, raising(httpx.ReadTimeout("slow")))

    with pytest.raises(AiServiceError) as caught:
        await ai_client.fetch_health(settings())

    assert str(caught.value) == "AI service did not answer the health check within 8 s"
    assert "unreachable" not in str(caught.value)


def test_the_health_timeout_is_above_the_ai_services_ollama_check() -> None:
    assert ai_client.HEALTH_TIMEOUT_SECONDS == 8.0  # the AI service gives Ollama 3 s


async def test_health_passes_on_ollama_down_and_the_chunk_count() -> None:
    ai_body = {
        "status": "degraded",
        "ollama": {"ok": False, "host": "h", "error": "cannot reach Ollama at h"},
        "models": {"llm": False, "embed": False, "missing": ["bge-m3:latest", "qwen3:8b"]},
        "qdrant": {"ok": True, "path": "p", "error": None},
        "knowledge_base": {"chunks": 0},
    }
    app = create_app()

    async def fake_health(_settings: Settings):
        from app.schemas.health import AiServiceHealth

        return AiServiceHealth.model_validate(ai_body)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(health_router, "fetch_health", fake_health)
        async with build_client(app) as client:
            response = await client.get("/api/health")

    body = response.json()
    assert body["status"] == "degraded"
    assert body["ai_service"]["ollama"]["error"] == "cannot reach Ollama at h"
    assert body["ai_service"]["knowledge_base"] == {"chunks": 0}


async def test_an_older_ai_service_without_a_chunk_count_still_validates() -> None:
    from app.schemas.health import AiServiceHealth

    health = AiServiceHealth.model_validate(
        {
            "status": "ok",
            "ollama": {"ok": True, "host": "h"},
            "models": {"llm": True, "embed": True},
            "qdrant": {"ok": True, "path": "p"},
        }
    )

    assert health.knowledge_base.chunks is None


# --- chat: one message per cause ----------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("ai_unreachable", AI_UNAVAILABLE),
        ("ollama_unreachable", MODEL_OFFLINE),
        ("model_missing", MODEL_OFFLINE),
        ("ollama_timeout", TOO_SLOW),
        ("ai_timeout", TOO_SLOW),
        ("ai_error", AI_UNAVAILABLE),
    ],
)
@pytest.mark.usefixtures("db_tables")
async def test_each_chat_failure_has_its_own_user_message(
    monkeypatch: pytest.MonkeyPatch, db: Session, code: str, expected: str
) -> None:
    auth_service.seed_admin(db, get_settings())

    async def failing(*_args: object) -> None:
        raise AiServiceError("technical reason, not for users", code)

    monkeypatch.setattr(ai_client, "send_chat", failing)

    async with build_client() as client:
        assert (await register(client, "u@example.com")).status_code == 201
        headers = bearer(await token_for(client, "u@example.com", USER_PASSWORD))
        response = await client.post(
            "/api/chat/message",
            json={"session_id": str(uuid4()), "message": "hello?"},
            headers=headers,
        )

    assert (response.status_code, response.json()) == (503, {"detail": expected})


# --- ingest through the real client mapping -----------------------------------------------


@pytest.mark.parametrize(
    ("ai_response", "stored"),
    [
        (
            httpx.Response(422, json={"detail": "scanned or image-only PDF is not supported"}),
            "scanned or image-only PDF is not supported",
        ),
        (
            httpx.Response(
                503,
                json={"detail": "model 'bge-m3' is not pulled - run: ollama pull bge-m3"},
                headers={"X-Error-Code": "model_missing"},
            ),
            "model 'bge-m3' is not pulled - run: ollama pull bge-m3 (AI service HTTP 503)",
        ),
        (
            httpx.Response(422, json={"detail": [{"msg": "Field required"}]}),
            "Field required",
        ),
    ],
)
@pytest.mark.usefixtures("db_tables")
async def test_a_failed_ingest_stores_the_ai_services_reason(
    monkeypatch: pytest.MonkeyPatch, db: Session, ai_response: httpx.Response, stored: str
) -> None:
    auth_service.seed_admin(db, get_settings())
    client = build_client()  # built before httpx is mocked for the AI service
    mock_ai(monkeypatch, lambda _r: ai_response)

    async with client:
        headers = bearer(await token_for(client, ADMIN_EMAIL, ADMIN_PASSWORD))
        response = await client.post(
            "/api/admin/documents", files={"file": ("n.txt", b"text")}, headers=headers
        )

    assert response.status_code == 201
    db.expire_all()
    document = db.scalars(select(Document)).one()
    assert (document.status, document.error) == ("failed", stored)


# --- middleware ---------------------------------------------------------------------------


@pytest.mark.usefixtures("db_tables")
async def test_an_oversized_upload_is_refused_before_the_body_is_read(
    monkeypatch: pytest.MonkeyPatch, db: Session
) -> None:
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    get_settings.cache_clear()
    auth_service.seed_admin(db, get_settings())

    async with build_client() as client:
        headers = bearer(await token_for(client, ADMIN_EMAIL, ADMIN_PASSWORD))
        response = await client.post(
            "/api/admin/documents",
            files={"file": ("big.txt", b"x" * (3 * 1024 * 1024))},
            headers={**headers, "Origin": ORIGIN},
        )

    assert response.status_code == 413
    assert response.json() == {"detail": "file is larger than the 1 MB upload limit"}
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert db.scalars(select(Document)).all() == []


@pytest.mark.usefixtures("db_tables")
async def test_a_file_just_over_the_limit_still_meets_the_streaming_check(
    monkeypatch: pytest.MonkeyPatch, db: Session
) -> None:
    """Inside the multipart slack, the route's own check is what refuses it."""
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    get_settings.cache_clear()
    auth_service.seed_admin(db, get_settings())

    async with build_client() as client:
        headers = bearer(await token_for(client, ADMIN_EMAIL, ADMIN_PASSWORD))
        response = await client.post(
            "/api/admin/documents",
            files={"file": ("big.txt", b"x" * (1024 * 1024 + 10))},
            headers=headers,
        )

    assert response.status_code == 413
    assert response.json() == {"detail": "file is larger than the 1 MB upload limit"}


def app_with_a_broken_route() -> FastAPI:
    app = create_app()

    async def boom() -> None:
        raise RuntimeError("secret internals")

    app.add_api_route("/api/boom", boom)
    return app


async def test_an_unhandled_error_is_a_json_500_with_cors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with build_client(app_with_a_broken_route()) as client:
        response = await client.get("/api/boom", headers={"Origin": ORIGIN})

    assert response.status_code == 500
    assert response.json() == {"detail": SERVER_ERROR}
    assert "secret internals" not in response.text
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert "unhandled error on GET /api/boom" in caplog.text


# --- settings -----------------------------------------------------------------------------


def test_a_short_jwt_secret_is_flagged() -> None:
    assert Settings(_env_file=None, jwt_secret="short").jwt_secret_too_short
    assert not Settings(_env_file=None, jwt_secret="x" * 32).jwt_secret_too_short


@pytest.mark.usefixtures("db_tables")
async def test_startup_warns_about_a_short_jwt_secret(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("JWT_SECRET", "too-short")
    get_settings.cache_clear()
    app = create_app()

    with caplog.at_level(logging.WARNING):
        async with app.router.lifespan_context(app):
            pass

    assert "JWT_SECRET is shorter than 32 bytes" in caplog.text


def test_max_upload_mb_must_be_positive() -> None:
    with pytest.raises(ValidationError, match="MAX_UPLOAD_MB"):
        Settings(_env_file=None, max_upload_mb=0)


# --- upload limits endpoint ---------------------------------------------------------------


@pytest.mark.usefixtures("db_tables")
async def test_the_upload_limit_comes_from_the_real_settings(
    monkeypatch: pytest.MonkeyPatch, db: Session
) -> None:
    monkeypatch.setenv("MAX_UPLOAD_MB", "35")
    get_settings.cache_clear()
    auth_service.seed_admin(db, get_settings())

    async with build_client() as client:
        headers = bearer(await token_for(client, ADMIN_EMAIL, ADMIN_PASSWORD))
        response = await client.get("/api/admin/documents/limits", headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "max_upload_mb": 35,
        "accepted_extensions": [".csv", ".docx", ".md", ".pdf", ".txt"],
    }


@pytest.mark.usefixtures("db_tables")
async def test_the_upload_limit_is_admin_only(db: Session) -> None:
    auth_service.seed_admin(db, get_settings())

    async with build_client() as client:
        anonymous = await client.get("/api/admin/documents/limits")
        assert (await register(client, "u2@example.com")).status_code == 201
        headers = bearer(await token_for(client, "u2@example.com", USER_PASSWORD))
        as_user = await client.get("/api/admin/documents/limits", headers=headers)

    assert (anonymous.status_code, as_user.status_code) == (401, 403)
