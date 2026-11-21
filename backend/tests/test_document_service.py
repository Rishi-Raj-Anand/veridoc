"""Upload storage and filename handling (CLAUDE.md §17 task 2.7)."""

from __future__ import annotations

import hashlib
import io

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Document, User
from app.services import auth_service
from app.services.document_service import (
    INTERRUPTED_MESSAGE,
    UploadRejected,
    display_name,
    mark_interrupted_ingests,
    sanitize_filename,
    save_upload,
)

pytestmark = pytest.mark.usefixtures("db_tables")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd.txt", "passwd.txt"),
        ("C:\\Users\\me\\My File (1).PDF", "My_File_1.pdf"),
        ("name with spaces.md", "name_with_spaces.md"),
        (".hidden.txt", "hidden.txt"),
        ("résumé.docx", "r_sum.docx"),
        ("README", "README"),
        ("weird.tar.gz", "weird.tar.gz"),
        ("", "document"),
        ("///", "document"),
        ("..\\..\\", "document"),
    ],
)
def test_sanitize_filename(raw: str, expected: str) -> None:
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_caps_length_and_keeps_the_extension() -> None:
    result = sanitize_filename("a" * 300 + ".pdf")

    assert len(result) <= 100
    assert result.endswith(".pdf")


def test_sanitized_names_never_contain_path_characters() -> None:
    for raw in ("a/b\\c.txt", "x:y*z?.md", "<script>.pdf", "tab\there.txt"):
        result = sanitize_filename(raw)
        assert not set(result) & set('/\\:*?"<>|\t ')


def test_display_name_keeps_the_original_but_drops_the_path() -> None:
    assert display_name("C:\\docs\\Fever Guide (2024).pdf") == "Fever Guide (2024).pdf"
    assert display_name("../x/notes.txt") == "notes.txt"
    assert display_name("") == "document"


def admin_id(db: Session, settings: Settings) -> int:
    auth_service.seed_admin(db, settings)
    return db.scalar(select(User.id).where(User.role == "admin"))


def upload(db: Session, settings: Settings, content: bytes, name: str = "note.txt") -> Document:
    return save_upload(db, settings, io.BytesIO(content), name, admin_id(db, settings))


def test_save_upload_stores_the_file_under_its_id(db: Session, settings: Settings) -> None:
    document = upload(db, settings, b"hello world", "My Note.txt")

    assert document.status == "pending"
    assert document.chunk_count == 0
    assert document.filename == "My Note.txt"
    assert document.sha256 == hashlib.sha256(b"hello world").hexdigest()
    assert document.size_bytes == 11
    assert document.stored_path == f"uploads/{document.id}_My_Note.txt"
    assert (settings.data_dir / document.stored_path).read_bytes() == b"hello world"


def test_windows_reserved_names_are_safe_because_of_the_id_prefix(
    db: Session, settings: Settings
) -> None:
    document = upload(db, settings, b"x", "CON.txt")

    assert document.stored_path == f"uploads/{document.id}_CON.txt"


def test_duplicate_content_is_rejected_and_leaves_no_stray_files(
    db: Session, settings: Settings
) -> None:
    upload(db, settings, b"same bytes", "a.txt")

    with pytest.raises(UploadRejected) as excinfo:
        upload(db, settings, b"same bytes", "b.txt")

    assert excinfo.value.status_code == 409
    assert [p.name for p in settings.uploads_dir.iterdir()] == [
        p.name for p in settings.uploads_dir.glob("1_*")
    ]


@pytest.mark.parametrize("name", ["x.exe", "x.png", "x.mp3", "noext", "archive.zip", "x.doc"])
def test_unsupported_extensions_are_415(db: Session, settings: Settings, name: str) -> None:
    with pytest.raises(UploadRejected) as excinfo:
        upload(db, settings, b"data", name)

    assert excinfo.value.status_code == 415


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("fake.pdf", b"just some text"),
        ("fake.docx", b"%PDF-1.7"),
        ("fake.txt", b"binary\x00data"),
        ("fake.md", b"\x00\x01\x02"),
    ],
)
def test_content_must_match_the_extension(
    db: Session, settings: Settings, name: str, content: bytes
) -> None:
    with pytest.raises(UploadRejected) as excinfo:
        upload(db, settings, content, name)

    assert excinfo.value.status_code == 415


def test_empty_file_is_422(db: Session, settings: Settings) -> None:
    with pytest.raises(UploadRejected) as excinfo:
        upload(db, settings, b"", "empty.txt")

    assert excinfo.value.status_code == 422


def test_oversize_file_is_413_and_cleaned_up(db: Session) -> None:
    small = Settings(_env_file=None, max_upload_mb=1)
    uid = admin_id(db, small)

    with pytest.raises(UploadRejected) as excinfo:
        save_upload(db, small, io.BytesIO(b"x" * (1024 * 1024 + 1)), "big.txt", uid)

    assert excinfo.value.status_code == 413
    assert list(small.uploads_dir.iterdir()) == []


def test_file_exactly_at_the_limit_is_accepted(db: Session) -> None:
    small = Settings(_env_file=None, max_upload_mb=1)
    uid = admin_id(db, small)

    document = save_upload(db, small, io.BytesIO(b"x" * (1024 * 1024)), "edge.txt", uid)

    assert document.size_bytes == 1024 * 1024


def test_mark_interrupted_ingests_fails_only_pending_rows(db: Session, settings: Settings) -> None:
    pending = upload(db, settings, b"one", "a.txt")
    ready = upload(db, settings, b"two", "b.txt")
    ready.status = "ready"
    db.commit()

    assert mark_interrupted_ingests(db) == 1

    db.refresh(pending)
    db.refresh(ready)
    assert (pending.status, pending.error) == ("failed", INTERRUPTED_MESSAGE)
    assert ready.status == "ready"


def test_a_csv_is_accepted_and_stored_like_text(db: Session, settings: Settings) -> None:
    document = upload(db, settings, b"Disease,Symptoms\nMalaria,fever\n", "Diseases.csv")

    assert document.status == "pending"
    assert document.stored_path == f"uploads/{document.id}_Diseases.csv"


def test_a_binary_file_named_csv_is_415(db: Session, settings: Settings) -> None:
    with pytest.raises(UploadRejected) as excinfo:
        upload(db, settings, b"a,b" + bytes([0]) + b"c", "fake.csv")

    assert excinfo.value.status_code == 415
