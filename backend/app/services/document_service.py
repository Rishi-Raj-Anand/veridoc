"""Document upload handling (CLAUDE.md §6.1.1, §17 task 2.7).

Validates the extension, size and content signature, de-duplicates by SHA-256, stores the
file as ``<doc_id>_<safe_name>`` and inserts the ``documents`` row with ``status="pending"``.
Nothing here talks HTTP: failures raise ``UploadRejected`` and the router maps them.
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
import uuid
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Document

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = frozenset({".pdf", ".txt", ".md", ".docx", ".csv"})
CHUNK_BYTES = 1024 * 1024
MAX_SAFE_NAME_CHARS = 100
MAX_DISPLAY_NAME_CHARS = 255
TEXT_SNIFF_BYTES = 8192
INTERRUPTED_MESSAGE = "ingestion was interrupted by a backend restart - delete and re-upload"


class UploadRejected(Exception):
    """The upload was refused. ``detail`` is safe to show to the admin."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _last_component(name: str) -> str:
    """Basename after splitting on both ``/`` and ``\\`` - never trust client paths."""
    return re.split(r"[\\/]", name.replace("\x00", ""))[-1].strip()


def display_name(name: str) -> str:
    """The original file name for the UI and citations, minus any path or control chars."""
    cleaned = "".join(ch for ch in _last_component(name) if ch.isprintable())
    return cleaned[:MAX_DISPLAY_NAME_CHARS] or "document"


def sanitize_filename(name: str) -> str:
    """A filesystem-safe name for ``data/uploads``: ``[A-Za-z0-9._-]`` only, extension kept."""
    base = unicodedata.normalize("NFKC", _last_component(name))
    path = Path(base)
    suffix = re.sub(r"[^A-Za-z0-9.]", "", path.suffix).lower()
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem if path.suffix else base)
    stem = re.sub(r"_+", "_", stem).lstrip("._-").strip("_")
    stem = stem[: max(1, MAX_SAFE_NAME_CHARS - len(suffix))] or "document"
    return f"{stem}{suffix}"


def too_large_message(max_bytes: int) -> str:
    """The 413 text, shared with the early ``Content-Length`` check in ``app.middleware``."""
    return f"file is larger than the {max_bytes // (1024 * 1024)} MB upload limit"


def _stream_to_file(source: BinaryIO, target: Path, max_bytes: int) -> tuple[str, int, bytes]:
    """Copy ``source`` to ``target`` hashing as we go; abort as soon as the limit is passed."""
    digest = hashlib.sha256()
    size = 0
    head = b""
    with target.open("wb") as out:
        while chunk := source.read(CHUNK_BYTES):
            size += len(chunk)
            if size > max_bytes:
                raise UploadRejected(413, too_large_message(max_bytes))
            if not head:
                head = chunk[:TEXT_SNIFF_BYTES]
            digest.update(chunk)
            out.write(chunk)
    return digest.hexdigest(), size, head


def _check_signature(suffix: str, head: bytes) -> None:
    """A cheap content check, so a renamed binary is refused rather than half-parsed."""
    if suffix == ".pdf" and not head.startswith(b"%PDF-"):
        raise UploadRejected(415, "file is not a valid PDF")
    if suffix == ".docx" and not head.startswith(b"PK\x03\x04"):
        raise UploadRejected(415, "file is not a valid DOCX")
    if suffix in {".txt", ".md", ".csv"} and b"\x00" in head:
        raise UploadRejected(415, "file does not look like text")


def save_upload(
    db: Session, settings: Settings, source: BinaryIO, original_name: str, uploaded_by: int
) -> Document:
    """Validate and store an upload, returning the new ``pending`` document row."""
    suffix = Path(_last_component(original_name)).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise UploadRejected(
            415, f"unsupported file type '{suffix or 'none'}' - allowed: {allowed}"
        )

    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    temp = settings.uploads_dir / f".upload-{uuid.uuid4().hex}.tmp"
    final: Path | None = None
    try:
        sha256, size, head = _stream_to_file(source, temp, settings.max_upload_bytes)
        if size == 0:
            raise UploadRejected(422, "the uploaded file is empty")
        _check_signature(suffix, head)
        if db.scalar(select(Document.id).where(Document.sha256 == sha256)) is not None:
            raise UploadRejected(409, "this document has already been uploaded")

        document = Document(
            filename=display_name(original_name),
            stored_path="",
            sha256=sha256,
            size_bytes=size,
            status="pending",
            uploaded_by=uploaded_by,
        )
        db.add(document)
        db.flush()  # assigns the id used in the stored file name
        final = settings.uploads_dir / f"{document.id}_{sanitize_filename(original_name)}"
        temp.replace(final)
        document.stored_path = final.relative_to(settings.data_dir).as_posix()
        db.commit()
    except IntegrityError as exc:
        # two identical uploads racing: the unique index on sha256 caught the loser
        db.rollback()
        raise UploadRejected(409, "this document has already been uploaded") from exc
    except BaseException:
        db.rollback()
        if final is not None:
            final.unlink(missing_ok=True)
        raise
    finally:
        temp.unlink(missing_ok=True)
    logger.info("stored upload id=%d name=%s bytes=%d", document.id, document.filename, size)
    return document


def absolute_path(settings: Settings, document: Document) -> Path:
    return settings.data_dir / document.stored_path


def delete_document_file(settings: Settings, document: Document) -> None:
    absolute_path(settings, document).unlink(missing_ok=True)


def mark_interrupted_ingests(db: Session) -> int:
    """A restart kills in-flight background ingests; flip their rows so they are not stuck."""
    result = db.execute(
        update(Document)
        .where(Document.status == "pending")
        .values(status="failed", error=INTERRUPTED_MESSAGE)
    )
    db.commit()
    return result.rowcount
