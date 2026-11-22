"""Parent-chunk docstore (CLAUDE.md §6.1.6).

Plain ``sqlite3``: the AI service never touches ``app.db`` and has no SQLAlchemy. Each
operation opens its own short-lived connection, so ``asyncio.to_thread`` workers never
share one.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

from ai_service.ingestion.chunker import ParentChunk

SCHEMA = """
CREATE TABLE IF NOT EXISTS parents (
    parent_id   TEXT PRIMARY KEY,
    doc_id      INTEGER NOT NULL,
    parent_index INTEGER NOT NULL,
    filename    TEXT NOT NULL,
    page_start  INTEGER NOT NULL,
    page_end    INTEGER NOT NULL,
    text        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_parents_doc_id ON parents (doc_id);
"""


@dataclass(frozen=True)
class StoredParent:
    parent_id: str
    doc_id: int
    filename: str
    page_start: int
    page_end: int
    text: str


class Docstore:
    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """A connection that commits on success, rolls back on error, and always closes."""
        with closing(sqlite3.connect(self._path, timeout=30)) as conn, conn:
            yield conn

    def replace_document(self, doc_id: int, filename: str, parents: Iterable[ParentChunk]) -> int:
        """Swap in the parents of ``doc_id`` atomically."""
        rows = [
            (p.parent_id, doc_id, p.index, filename, p.page_start, p.page_end, p.text)
            for p in parents
        ]
        with self._connect() as conn:
            conn.execute("DELETE FROM parents WHERE doc_id = ?", (doc_id,))
            conn.executemany("INSERT INTO parents VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        return len(rows)

    def get_parents(self, parent_ids: Sequence[str]) -> list[StoredParent]:
        """Fetch parents in the order requested (Phase 4 orders them by child score)."""
        if not parent_ids:
            return []
        marks = ",".join("?" * len(parent_ids))
        with self._connect() as conn:
            found = {
                row[0]: StoredParent(row[0], row[1], row[2], row[3], row[4], row[5])
                for row in conn.execute(
                    "SELECT parent_id, doc_id, filename, page_start, page_end, text "
                    f"FROM parents WHERE parent_id IN ({marks})",
                    tuple(parent_ids),
                )
            }
        return [found[pid] for pid in parent_ids if pid in found]

    def filenames(self) -> set[str]:
        """Every indexed filename (the evaluation checks test-set sources against these)."""
        with self._connect() as conn:
            return {row[0] for row in conn.execute("SELECT DISTINCT filename FROM parents")}

    def count_document(self, doc_id: int) -> int:
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM parents WHERE doc_id = ?", (doc_id,)
            ).fetchone()[0]

    def delete_document(self, doc_id: int) -> int:
        with self._connect() as conn:
            return conn.execute("DELETE FROM parents WHERE doc_id = ?", (doc_id,)).rowcount
