"""POST /internal/ingest and DELETE /internal/documents (CLAUDE.md §9, §6.1)."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from ai_service.config import get_settings
from ai_service.retrieval.vector_store import COLLECTION
from tests.helpers import FakeEmbedder, build_app, internal_client

LONG_TEXT = "\n\n".join(
    f"Paragraph {i}: " + "Paracetamol relieves mild pain and lowers fever. " * 12 for i in range(14)
)


def write_upload(name: str, content: str | bytes = LONG_TEXT) -> Path:
    uploads = get_settings().uploads_dir
    uploads.mkdir(parents=True, exist_ok=True)
    path = uploads / name
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_bytes(content)
    return path


def body(doc_id: int, path: Path, filename: str | None = None) -> dict:
    return {"doc_id": doc_id, "file_path": str(path), "filename": filename or path.name}


async def test_ingest_indexes_children_and_parents(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_app(monkeypatch)
    path = write_upload("1_notes.txt")

    async with internal_client(app) as client:
        response = await client.post("/internal/ingest", json=body(1, path, "notes.txt"))

    assert response.status_code == 200
    chunk_count = response.json()["chunk_count"]
    assert chunk_count > 1
    assert app.state.vector_store.count_document(1) == chunk_count
    assert app.state.docstore.count_document(1) >= 1
    payload = app.state.vector_store._client.scroll(COLLECTION, with_payload=True)[0][0].payload
    assert payload["filename"] == "notes.txt"
    assert payload["parent_id"].startswith("1:p")


async def test_all_children_are_embedded_in_one_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_app(monkeypatch)
    path = write_upload("1_notes.txt")

    async with internal_client(app) as client:
        response = await client.post("/internal/ingest", json=body(1, path))

    assert len(app.state.llm.calls) == 1
    assert len(app.state.llm.calls[0]) == response.json()["chunk_count"]


async def test_reingesting_the_same_document_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_app(monkeypatch)
    path = write_upload("1_notes.txt")

    async with internal_client(app) as client:
        first = await client.post("/internal/ingest", json=body(1, path))
        second = await client.post("/internal/ingest", json=body(1, path))

    assert first.json() == second.json()
    assert app.state.vector_store.count_document(1) == first.json()["chunk_count"]


async def test_delete_removes_points_and_parents_for_that_document_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_app(monkeypatch)
    one, two = write_upload("1_a.txt"), write_upload("2_b.txt")

    async with internal_client(app) as client:
        await client.post("/internal/ingest", json=body(1, one))
        await client.post("/internal/ingest", json=body(2, two))
        kept = app.state.vector_store.count_document(2)
        deleted = await client.delete("/internal/documents/1")

    assert deleted.status_code == 200
    assert deleted.json()["deleted_points"] > 0
    assert deleted.json()["deleted_parents"] > 0
    assert app.state.vector_store.count_document(1) == 0
    assert app.state.docstore.count_document(1) == 0
    assert app.state.vector_store.count_document(2) == kept


async def test_delete_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_app(monkeypatch)

    async with internal_client(app) as client:
        response = await client.delete("/internal/documents/404")

    assert response.status_code == 200
    assert response.json() == {"deleted_points": 0, "deleted_parents": 0}


async def test_path_outside_uploads_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = build_app(monkeypatch)
    outside = tmp_path / "secret.txt"
    outside.write_text("not for ingestion", encoding="utf-8")

    async with internal_client(app) as client:
        response = await client.post("/internal/ingest", json=body(1, outside))

    assert response.status_code == 400
    assert app.state.llm.calls == []


async def test_traversal_out_of_uploads_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_app(monkeypatch)
    write_upload("1_a.txt")
    sneaky = get_settings().uploads_dir / ".." / "app.db"

    async with internal_client(app) as client:
        response = await client.post("/internal/ingest", json=body(1, sneaky))

    assert response.status_code == 400


async def test_missing_file_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_app(monkeypatch)
    missing = get_settings().uploads_dir / "9_gone.txt"

    async with internal_client(app) as client:
        response = await client.post("/internal/ingest", json=body(9, missing))

    assert response.status_code == 404


async def test_scanned_pdf_is_422_with_the_exact_message(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_app(monkeypatch)
    pdf = pymupdf.open()
    pdf.new_page()
    path = write_upload("1_scan.pdf", b"")
    pdf.save(path)
    pdf.close()

    async with internal_client(app) as client:
        response = await client.post("/internal/ingest", json=body(1, path))

    assert response.status_code == 422
    assert response.json()["detail"] == "scanned or image-only PDF is not supported"
    assert app.state.vector_store.count_document(1) == 0


async def test_ollama_down_is_503_and_nothing_is_written(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_app(monkeypatch)
    app.state.llm = FakeEmbedder(fails=True)
    path = write_upload("1_notes.txt")

    async with internal_client(app) as client:
        response = await client.post("/internal/ingest", json=body(1, path))

    assert response.status_code == 503
    assert "Ollama" in response.json()["detail"]
    assert app.state.vector_store.count_document(1) == 0
    assert app.state.docstore.count_document(1) == 0


async def test_a_failed_reingest_keeps_the_previous_index(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_app(monkeypatch)
    path = write_upload("1_notes.txt")
    async with internal_client(app) as client:
        first = await client.post("/internal/ingest", json=body(1, path))
        app.state.llm = FakeEmbedder(fails=True)
        failed = await client.post("/internal/ingest", json=body(1, path))

    assert failed.status_code == 503
    assert app.state.vector_store.count_document(1) == first.json()["chunk_count"]


async def test_storage_failure_rolls_back_both_stores(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_app(monkeypatch)
    path = write_upload("1_notes.txt")

    def boom(*_args: object, **_kwargs: object) -> int:
        raise RuntimeError("disk full")

    monkeypatch.setattr(app.state.vector_store, "upsert_children", boom)

    async with internal_client(app) as client:
        response = await client.post("/internal/ingest", json=body(1, path))

    assert response.status_code == 500
    # The raw exception text stays in the log; the admin gets a plain sentence (Phase 8.1).
    detail = response.json()["detail"]
    assert detail == "indexing failed unexpectedly (RuntimeError) - see the AI service log"
    assert "disk full" not in detail
    assert app.state.docstore.count_document(1) == 0
    assert app.state.vector_store.count_document(1) == 0


async def test_both_routes_require_the_internal_key(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_app(monkeypatch)
    path = write_upload("1_notes.txt")

    async with internal_client(app, key=None) as client:
        ingest = await client.post("/internal/ingest", json=body(1, path))
        delete = await client.delete("/internal/documents/1")

    assert ingest.status_code == 401
    assert delete.status_code == 401
