"""Admin document routes (CLAUDE.md §9, §17 tasks 2.9-2.10)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.main import create_app
from app.models import Document, User
from app.schemas.document import IngestResult
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

pytestmark = pytest.mark.usefixtures("db_tables")

URL = "/api/admin/documents"
PDF_BYTES = b"%PDF-1.7 pretend pdf body"
DOCX_BYTES = b"PK\x03\x04 pretend docx body"


class FakeAi:
    """Stands in for the AI service calls the router makes."""

    def __init__(self) -> None:
        self.ingested: list[tuple[int, str, str]] = []
        self.deleted: list[int] = []
        self.ingest_error: Exception | None = None
        self.delete_error: Exception | None = None
        self.chunk_count = 7

    async def ingest(self, _settings: Settings, doc_id: int, file_path: str, filename: str):
        self.ingested.append((doc_id, file_path, filename))
        if self.ingest_error is not None:
            raise self.ingest_error
        return IngestResult(chunk_count=self.chunk_count)

    async def delete(self, _settings: Settings, doc_id: int) -> None:
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.append(doc_id)


@pytest.fixture
def fake_ai(monkeypatch: pytest.MonkeyPatch) -> FakeAi:
    fake = FakeAi()
    monkeypatch.setattr(ai_client, "ingest_document", fake.ingest)
    monkeypatch.setattr(ai_client, "delete_document_index", fake.delete)
    return fake


@pytest.fixture
def client(db: Session, settings: Settings, fake_ai: FakeAi) -> httpx.AsyncClient:
    auth_service.seed_admin(db, settings)
    return build_client(create_app())


async def admin_headers(client: httpx.AsyncClient) -> dict[str, str]:
    return bearer(await token_for(client, ADMIN_EMAIL, ADMIN_PASSWORD))


async def upload(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    name: str = "note.txt",
    content: bytes = b"hello medical world",
) -> httpx.Response:
    return await client.post(URL, files={"file": (name, content)}, headers=headers)


# --- upload: happy paths ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("note.txt", b"plain text"),
        ("guide.md", b"# Heading\n\ntext"),
        ("ref.pdf", PDF_BYTES),
        ("UPPER.PDF", PDF_BYTES + b" upper"),
        ("report.docx", DOCX_BYTES),
    ],
)
async def test_each_allowed_type_is_accepted_as_pending(
    client: httpx.AsyncClient, name: str, content: bytes
) -> None:
    async with client:
        response = await upload(client, await admin_headers(client), name, content)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["filename"] == name
    assert body["chunk_count"] == 0
    assert "stored_path" not in body and "sha256" not in body


async def test_the_file_lands_in_uploads_as_id_and_safe_name(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    async with client:
        response = await upload(client, await admin_headers(client), "My Guide (v2).pdf", PDF_BYTES)

    doc_id = response.json()["id"]
    assert (settings.uploads_dir / f"{doc_id}_My_Guide_v2.pdf").read_bytes() == PDF_BYTES


async def test_a_hostile_filename_cannot_escape_the_uploads_folder(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    async with client:
        response = await upload(
            client, await admin_headers(client), "../../../evil.txt", b"not evil really"
        )

    assert response.status_code == 201
    stored = list(settings.data_dir.rglob("*evil*"))
    assert [p.parent for p in stored] == [settings.uploads_dir]


async def test_background_ingest_flips_the_row_to_ready(
    client: httpx.AsyncClient, fake_ai: FakeAi, settings: Settings
) -> None:
    async with client:
        headers = await admin_headers(client)
        created = await upload(client, headers, "ref.pdf", PDF_BYTES)
        listed = await client.get(URL, headers=headers)

    doc_id = created.json()["id"]
    row = listed.json()[0]
    assert (row["id"], row["status"], row["chunk_count"], row["error"]) == (
        doc_id,
        "ready",
        7,
        None,
    )
    (called_id, called_path, called_name) = fake_ai.ingested[0]
    assert called_id == doc_id and called_name == "ref.pdf"
    assert Path(called_path) == settings.uploads_dir / f"{doc_id}_ref.pdf"


async def test_ai_failure_marks_the_row_failed_with_the_reason(
    client: httpx.AsyncClient, fake_ai: FakeAi
) -> None:
    fake_ai.ingest_error = AiServiceError("scanned or image-only PDF is not supported")
    async with client:
        headers = await admin_headers(client)
        await upload(client, headers, "scan.pdf", PDF_BYTES)
        row = (await client.get(URL, headers=headers)).json()[0]

    assert row["status"] == "failed"
    assert row["error"] == "scanned or image-only PDF is not supported"


async def test_unexpected_ingest_crash_still_ends_in_failed(
    client: httpx.AsyncClient, fake_ai: FakeAi
) -> None:
    fake_ai.ingest_error = RuntimeError("kaboom")
    async with client:
        headers = await admin_headers(client)
        await upload(client, headers)
        row = (await client.get(URL, headers=headers)).json()[0]

    assert row["status"] == "failed"
    assert "kaboom" in row["error"]


async def test_a_long_error_is_truncated(client: httpx.AsyncClient, fake_ai: FakeAi) -> None:
    fake_ai.ingest_error = AiServiceError("x" * 5000)
    async with client:
        headers = await admin_headers(client)
        await upload(client, headers)
        row = (await client.get(URL, headers=headers)).json()[0]

    assert len(row["error"]) == 2000


# --- upload: validation matrix ---------------------------------------------------------


@pytest.mark.parametrize("name", ["virus.exe", "photo.png", "song.mp3", "clip.mp4", "noext"])
async def test_unsupported_types_are_415(client: httpx.AsyncClient, name: str) -> None:
    async with client:
        response = await upload(client, await admin_headers(client), name, b"data")

    assert response.status_code == 415


async def test_a_pdf_that_is_really_text_is_415(client: httpx.AsyncClient) -> None:
    async with client:
        response = await upload(client, await admin_headers(client), "fake.pdf", b"hello")

    assert response.status_code == 415
    assert "PDF" in response.json()["detail"]


async def test_empty_file_is_422(client: httpx.AsyncClient) -> None:
    async with client:
        response = await upload(client, await admin_headers(client), "empty.txt", b"")

    assert response.status_code == 422


async def test_oversize_file_is_413(
    monkeypatch: pytest.MonkeyPatch, db: Session, fake_ai: FakeAi
) -> None:
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    get_settings.cache_clear()
    auth_service.seed_admin(db, get_settings())

    async with build_client(create_app()) as client:
        headers = await admin_headers(client)
        response = await upload(client, headers, "big.txt", b"x" * (1024 * 1024 + 1))

    assert response.status_code == 413
    assert "1 MB" in response.json()["detail"]
    assert fake_ai.ingested == []


async def test_duplicate_content_is_409_even_under_another_name(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        headers = await admin_headers(client)
        first = await upload(client, headers, "a.txt", b"identical bytes")
        second = await upload(client, headers, "renamed.txt", b"identical bytes")
        listed = await client.get(URL, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 409
    assert len(listed.json()) == 1


async def test_missing_file_part_is_422(client: httpx.AsyncClient) -> None:
    async with client:
        response = await client.post(URL, data={"x": "y"}, headers=await admin_headers(client))

    assert response.status_code == 422


# --- list ------------------------------------------------------------------------------


async def test_list_is_newest_first(client: httpx.AsyncClient) -> None:
    async with client:
        headers = await admin_headers(client)
        await upload(client, headers, "first.txt", b"one")
        await upload(client, headers, "second.txt", b"two")
        listed = await client.get(URL, headers=headers)

    assert [d["filename"] for d in listed.json()] == ["second.txt", "first.txt"]


async def test_list_of_nothing_is_an_empty_array(client: httpx.AsyncClient) -> None:
    async with client:
        response = await client.get(URL, headers=await admin_headers(client))

    assert response.json() == []


# --- delete ----------------------------------------------------------------------------


async def test_delete_removes_index_file_and_row(
    client: httpx.AsyncClient, fake_ai: FakeAi, settings: Settings, db: Session
) -> None:
    async with client:
        headers = await admin_headers(client)
        created = await upload(client, headers, "ref.pdf", PDF_BYTES)
        doc_id = created.json()["id"]
        response = await client.delete(f"{URL}/{doc_id}", headers=headers)

    assert response.status_code == 204
    assert fake_ai.deleted == [doc_id]
    assert not list(settings.uploads_dir.iterdir())
    db.expire_all()
    assert db.get(Document, doc_id) is None


async def test_a_deleted_document_can_be_uploaded_again(client: httpx.AsyncClient) -> None:
    async with client:
        headers = await admin_headers(client)
        doc_id = (await upload(client, headers, "a.txt", b"same")).json()["id"]
        await client.delete(f"{URL}/{doc_id}", headers=headers)
        again = await upload(client, headers, "a.txt", b"same")

    assert again.status_code == 201


async def test_delete_with_the_ai_service_down_removes_nothing(
    client: httpx.AsyncClient, fake_ai: FakeAi, settings: Settings, db: Session
) -> None:
    async with client:
        headers = await admin_headers(client)
        doc_id = (await upload(client, headers, "ref.pdf", PDF_BYTES)).json()["id"]
        fake_ai.delete_error = AiServiceError("AI service unavailable")
        response = await client.delete(f"{URL}/{doc_id}", headers=headers)

    assert response.status_code == 503
    assert "nothing was deleted" in response.json()["detail"]
    assert len(list(settings.uploads_dir.iterdir())) == 1
    db.expire_all()
    assert db.get(Document, doc_id) is not None


async def test_delete_of_a_pending_document_is_409(
    client: httpx.AsyncClient, fake_ai: FakeAi, db: Session
) -> None:
    admin = db.query(User).filter_by(role="admin").one()
    pending = Document(
        filename="busy.pdf",
        stored_path="uploads/1_busy.pdf",
        sha256=hashlib.sha256(b"busy").hexdigest(),
        size_bytes=4,
        status="pending",
        uploaded_by=admin.id,
    )
    db.add(pending)
    db.commit()

    async with client:
        response = await client.delete(f"{URL}/{pending.id}", headers=await admin_headers(client))

    assert response.status_code == 409
    assert fake_ai.deleted == []


async def test_delete_of_a_failed_document_works(
    client: httpx.AsyncClient, fake_ai: FakeAi
) -> None:
    fake_ai.ingest_error = AiServiceError("nope")
    async with client:
        headers = await admin_headers(client)
        doc_id = (await upload(client, headers)).json()["id"]
        response = await client.delete(f"{URL}/{doc_id}", headers=headers)

    assert response.status_code == 204


async def test_delete_of_an_unknown_document_is_404(client: httpx.AsyncClient) -> None:
    async with client:
        response = await client.delete(f"{URL}/999", headers=await admin_headers(client))

    assert response.status_code == 404


# --- guards ----------------------------------------------------------------------------


async def test_anonymous_requests_are_401(client: httpx.AsyncClient) -> None:
    async with client:
        responses = [
            await client.post(URL, files={"file": ("a.txt", b"x")}),
            await client.get(URL),
            await client.delete(f"{URL}/1"),
        ]

    assert [r.status_code for r in responses] == [401, 401, 401]


async def test_a_regular_user_is_403_everywhere(client: httpx.AsyncClient, fake_ai: FakeAi) -> None:
    async with client:
        await register(client)
        headers = bearer(await token_for(client, "user@example.com", USER_PASSWORD))
        responses = [
            await client.post(URL, files={"file": ("a.txt", b"x")}, headers=headers),
            await client.get(URL, headers=headers),
            await client.delete(f"{URL}/1", headers=headers),
        ]

    assert [r.status_code for r in responses] == [403, 403, 403]
    assert fake_ai.ingested == []


async def test_a_csv_upload_is_accepted_and_sent_for_ingestion(
    client: httpx.AsyncClient, fake_ai: FakeAi
) -> None:
    async with client:
        response = await upload(
            client, await admin_headers(client), "diseases.csv", b"Disease,Symptoms\nFlu,fever\n"
        )

    assert response.status_code == 201
    assert response.json()["filename"] == "diseases.csv"
    assert fake_ai.ingested[0][2] == "diseases.csv"


async def test_listed_times_are_marked_utc_so_browsers_show_local_time(
    client: httpx.AsyncClient,
) -> None:
    """SQLite drops the timezone; without a "Z" the browser shows UTC as if it were local."""
    async with client:
        headers = await admin_headers(client)
        uploaded = await upload(client, headers)
        listed = await client.get(URL, headers=headers)

    created = listed.json()[0]["created_at"]
    assert created.endswith("Z")
    assert created == uploaded.json()["created_at"]
