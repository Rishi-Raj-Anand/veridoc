"""Phase 8.1 error-handling sweep: each failure gives a specific, friendly answer over HTTP."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf
import pytest

from ai_service.config import FALLBACK_MESSAGE, get_settings
from ai_service.ingestion.chunker import ChildChunk
from ai_service.llm import ollama_client
from ai_service.llm.ollama_client import (
    ModelNotPulledError,
    OllamaError,
    OllamaTimeoutError,
    OllamaUnreachableError,
)
from ai_service.main import probe_ollama
from tests.helpers import (
    FakeEmbedder,
    FakeLlm,
    FakeOllamaClient,
    build_app,
    internal_client,
)

SESSION = "33333333-3333-4333-8333-333333333333"
VECTOR = [1.0] + [0.0] * 1023


def chat_body(message: str = "How is malaria prevented?") -> dict[str, Any]:
    return {"session_id": SESSION, "user_id": 3, "message": message}


def seed(app: Any) -> None:
    app.state.vector_store.upsert_children(
        1, "disease.md", [ChildChunk("1:p00000", 0, 1, "Use bed nets.")], [VECTOR]
    )


def write_upload(name: str, content: bytes) -> Path:
    uploads = get_settings().uploads_dir
    uploads.mkdir(parents=True, exist_ok=True)
    path = uploads / name
    path.write_bytes(content)
    return path


def ingest_body(doc_id: int, path: Path) -> dict[str, Any]:
    return {"doc_id": doc_id, "file_path": str(path), "filename": path.name.split("_", 1)[1]}


# --- chat ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (OllamaUnreachableError("cannot reach Ollama - is it running?"), "ollama_unreachable"),
        (ModelNotPulledError("model 'qwen3:8b' is not pulled"), "model_missing"),
        (OllamaTimeoutError("Ollama did not finish the chat within 120 s"), "ollama_timeout"),
        (OllamaError("Ollama chat failed: bad"), "ollama_error"),
    ],
)
async def test_chat_503_names_the_cause_in_a_header(
    monkeypatch: pytest.MonkeyPatch, error: OllamaError, code: str
) -> None:
    app = build_app(monkeypatch, llm=FakeLlm(query_vector=VECTOR, chat_error=error))
    seed(app)

    async with internal_client(app) as client:
        response = await client.post("/internal/chat", json=chat_body())

    assert response.status_code == 503
    assert response.headers["X-Error-Code"] == code
    assert response.json() == {"detail": str(error)}


async def test_an_unexpected_chat_failure_is_a_plain_500(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken(FakeLlm):
        def chat(self, *args: Any, **kwargs: Any) -> str:
            raise RuntimeError("secret internals")

    app = build_app(monkeypatch, llm=Broken(query_vector=VECTOR))
    seed(app)

    async with internal_client(app) as client:
        response = await client.post("/internal/chat", json=chat_body())

    assert response.status_code == 500
    assert response.json() == {"detail": "chat turn failed unexpectedly"}


async def test_an_empty_knowledge_base_falls_back_without_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = FakeLlm(query_vector=VECTOR, reply="never used")
    app = build_app(monkeypatch, llm=llm)

    async with internal_client(app) as client:
        response = await client.post("/internal/chat", json=chat_body())

    assert response.status_code == 200
    assert (response.json()["status"], response.json()["answer"]) == ("fallback", FALLBACK_MESSAGE)
    assert "embed" not in llm.events


# --- ingest -------------------------------------------------------------------------------


async def post_ingest(app: Any, path: Path) -> Any:
    async with internal_client(app) as client:
        return await client.post("/internal/ingest", json=ingest_body(1, path))


async def test_a_bad_docx_is_a_422_with_a_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_app(monkeypatch)
    path = write_upload("1_notes.docx", b"PK\x03\x04 this is not really a docx")

    response = await post_ingest(app, path)

    assert response.status_code == 422
    assert response.json()["detail"].startswith("could not open DOCX")


async def test_a_password_protected_pdf_is_a_422(monkeypatch: pytest.MonkeyPatch) -> None:
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "Paracetamol relieves pain. " * 20)
    data = pdf.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
    app = build_app(monkeypatch)

    response = await post_ingest(app, write_upload("1_locked.pdf", data))

    assert response.status_code == 422
    assert response.json()["detail"] == "password-protected PDF is not supported"


async def test_a_pdf_page_that_cannot_be_read_is_a_422(monkeypatch: pytest.MonkeyPatch) -> None:
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "Paracetamol relieves pain. " * 20)
    path = write_upload("1_damaged.pdf", pdf.tobytes())

    def broken(*_args: Any, **_kwargs: Any) -> str:
        raise RuntimeError("content stream is damaged")

    monkeypatch.setattr(pymupdf.Page, "get_text", broken)
    app = build_app(monkeypatch)

    response = await post_ingest(app, path)

    assert response.status_code == 422
    assert response.json()["detail"].startswith("could not read page 1 of the PDF")


async def test_a_csv_without_rows_is_a_422(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_app(monkeypatch)

    response = await post_ingest(app, write_upload("1_diseases.csv", b"Disease,Symptom\n"))

    assert response.status_code == 422
    assert response.json()["detail"].startswith("CSV could not be converted")


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (OllamaUnreachableError("cannot reach Ollama"), "ollama_unreachable"),
        (
            ModelNotPulledError("model 'bge-m3' is not pulled - run: ollama pull bge-m3"),
            "model_missing",
        ),
    ],
)
async def test_an_embedding_failure_is_a_503_with_its_code(
    monkeypatch: pytest.MonkeyPatch, error: OllamaError, code: str
) -> None:
    class Failing(FakeEmbedder):
        def embed(self, texts: list[str]) -> list[list[float]]:
            raise error

    app = build_app(monkeypatch, llm=Failing())

    response = await post_ingest(app, write_upload("1_notes.txt", b"Rest and fluids. " * 50))

    assert response.status_code == 503
    assert response.headers["X-Error-Code"] == code
    assert response.json()["detail"] == str(error)


async def test_a_delete_storage_failure_is_a_plain_500(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_app(monkeypatch)

    def boom(_doc_id: int) -> int:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(app.state.docstore, "delete_document", boom)

    async with internal_client(app) as client:
        response = await client.delete("/internal/documents/4")

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail.startswith("could not remove document 4 from the index (RuntimeError)")
    assert "database is locked" not in detail


# --- health -------------------------------------------------------------------------------


async def get_health(app: Any) -> dict[str, Any]:
    async with internal_client(app) as client:
        response = await client.get("/internal/health")
    assert response.status_code == 200
    return response.json()


async def test_health_names_a_missing_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    body = await get_health(build_app(monkeypatch, models=["bge-m3:latest"]))

    assert body["status"] == "degraded"
    assert body["models"] == {"llm": False, "embed": True, "missing": ["qwen3:8b"]}


async def test_health_names_a_missing_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    body = await get_health(build_app(monkeypatch, models=["qwen3:8b"]))

    assert body["models"] == {"llm": True, "embed": False, "missing": ["bge-m3:latest"]}


async def test_health_reports_the_indexed_chunk_count(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_app(monkeypatch, models=["qwen3:8b", "bge-m3:latest"])

    empty = await get_health(app)
    seed(app)
    seeded = await get_health(app)

    # An empty knowledge base is reported, but it is not "degraded": the service works.
    assert (empty["status"], empty["knowledge_base"]) == ("ok", {"chunks": 0})
    assert seeded["knowledge_base"] == {"chunks": 1}


async def test_health_has_no_chunk_count_when_qdrant_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = await get_health(build_app(monkeypatch, models=["qwen3:8b"], qdrant=None))

    assert body["knowledge_base"] == {"chunks": None}


# --- startup probe ------------------------------------------------------------------------


def use_ollama(monkeypatch: pytest.MonkeyPatch, models: list[str], *, fails: bool = False) -> None:
    monkeypatch.setattr(
        ollama_client.ollama, "Client", lambda **_kw: FakeOllamaClient(models, fails=fails)
    )


def test_the_startup_probe_is_quiet_when_all_is_well(monkeypatch: pytest.MonkeyPatch) -> None:
    use_ollama(monkeypatch, ["qwen3:8b", "bge-m3:latest"])

    assert probe_ollama(get_settings()) == []


def test_the_startup_probe_names_each_missing_model(monkeypatch: pytest.MonkeyPatch) -> None:
    use_ollama(monkeypatch, [])

    assert probe_ollama(get_settings()) == [
        "model qwen3:8b is not pulled - run: ollama pull qwen3:8b",
        "model bge-m3 is not pulled - run: ollama pull bge-m3",
    ]


def test_the_startup_probe_says_when_ollama_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    use_ollama(monkeypatch, [], fails=True)

    [warning] = probe_ollama(get_settings())

    assert warning.startswith("Ollama is not reachable at http://127.0.0.1:11434")
