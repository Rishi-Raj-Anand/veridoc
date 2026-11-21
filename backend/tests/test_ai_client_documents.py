"""``ai_client`` ingest and delete calls (CLAUDE.md §17 task 2.8)."""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.services import ai_client
from app.services.ai_client import AiServiceError


def settings() -> Settings:
    return Settings(_env_file=None, internal_api_key="shared-key", ai_service_url="http://ai:8001")


def mock_ai(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    class MockedClient(httpx.AsyncClient):
        def __init__(self, **kwargs: object) -> None:
            self.timeout_used = kwargs.pop("timeout", None)
            super().__init__(transport=httpx.MockTransport(wrapped), **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(ai_client.httpx, "AsyncClient", MockedClient)
    return seen


async def test_ingest_posts_the_contract_body_with_the_internal_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = mock_ai(monkeypatch, lambda _r: httpx.Response(200, json={"chunk_count": 12}))

    result = await ai_client.ingest_document(settings(), 3, "C:/data/uploads/3_a.pdf", "a.pdf")

    assert result.chunk_count == 12
    request = seen[0]
    assert (request.method, str(request.url)) == ("POST", "http://ai:8001/internal/ingest")
    assert request.headers["X-Internal-Key"] == "shared-key"
    assert request.read() == (
        b'{"doc_id":3,"file_path":"C:/data/uploads/3_a.pdf","filename":"a.pdf"}'
    )


async def test_ingest_surfaces_the_ai_services_own_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_ai(
        monkeypatch,
        lambda _r: httpx.Response(
            422, json={"detail": "scanned or image-only PDF is not supported"}
        ),
    )

    with pytest.raises(AiServiceError) as excinfo:
        await ai_client.ingest_document(settings(), 1, "p", "f.pdf")

    assert str(excinfo.value) == "scanned or image-only PDF is not supported"


async def test_ingest_maps_a_server_error_with_a_status_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_ai(monkeypatch, lambda _r: httpx.Response(503, json={"detail": "model not pulled"}))

    with pytest.raises(AiServiceError, match=r"model not pulled \(AI service HTTP 503\)"):
        await ai_client.ingest_document(settings(), 1, "p", "f.pdf")


async def test_wrong_key_gets_a_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_ai(monkeypatch, lambda _r: httpx.Response(401, json={"detail": "nope"}))

    with pytest.raises(AiServiceError, match="wrong INTERNAL_API_KEY"):
        await ai_client.ingest_document(settings(), 1, "p", "f.pdf")


async def test_unreachable_service_is_a_friendly_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    mock_ai(monkeypatch, refuse)

    with pytest.raises(AiServiceError, match=r"AI service unavailable.*is it running"):
        await ai_client.ingest_document(settings(), 1, "p", "f.pdf")


async def test_garbage_ingest_body_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_ai(monkeypatch, lambda _r: httpx.Response(200, json={"ok": True}))

    with pytest.raises(AiServiceError, match="unexpected ingest response"):
        await ai_client.ingest_document(settings(), 1, "p", "f.pdf")


async def test_delete_calls_the_document_route(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = mock_ai(
        monkeypatch,
        lambda _r: httpx.Response(200, json={"deleted_points": 4, "deleted_parents": 2}),
    )

    await ai_client.delete_document_index(settings(), 9)

    assert (seen[0].method, str(seen[0].url)) == ("DELETE", "http://ai:8001/internal/documents/9")
    assert seen[0].headers["X-Internal-Key"] == "shared-key"


async def test_delete_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_ai(monkeypatch, lambda _r: httpx.Response(500, json={"detail": "boom"}))

    with pytest.raises(AiServiceError, match="boom"):
        await ai_client.delete_document_index(settings(), 9)


async def test_ingest_waits_for_the_configured_number_of_minutes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeouts: list[object] = []

    class RecordingClient(httpx.AsyncClient):
        def __init__(self, **kwargs: object) -> None:
            timeouts.append(kwargs.pop("timeout", None))
            transport = httpx.MockTransport(lambda _r: httpx.Response(200, json={"chunk_count": 1}))
            super().__init__(transport=transport, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(ai_client.httpx, "AsyncClient", RecordingClient)
    configured = Settings(
        _env_file=None,
        internal_api_key="shared-key",
        ai_service_url="http://ai:8001",
        ingest_timeout_minutes=45,
    )

    await ai_client.ingest_document(configured, 1, "p", "f.csv")

    assert timeouts == [45 * 60.0]


async def test_an_ingest_that_runs_too_long_says_so_instead_of_is_it_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def slow(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    mock_ai(monkeypatch, slow)

    with pytest.raises(AiServiceError) as excinfo:
        await ai_client.ingest_document(settings(), 1, "p", "big.csv")

    message = str(excinfo.value)
    assert message.startswith("Indexing did not finish within 60 minutes")
    assert "INGEST_TIMEOUT_MINUTES" in message
    assert "is it running" not in message


async def test_other_slow_calls_get_a_plain_timeout_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def slow(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    mock_ai(monkeypatch, slow)

    with pytest.raises(AiServiceError, match=r"did not answer within 30 seconds"):
        await ai_client.delete_document_index(settings(), 1)
