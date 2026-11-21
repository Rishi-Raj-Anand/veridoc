"""Atomic file writes shared by the transcript and behaviour stores (CLAUDE.md §6.7)."""

from __future__ import annotations

import os
import time
from pathlib import Path

REPLACE_ATTEMPTS = 3
REPLACE_DELAY_SECONDS = 0.1


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` so a reader never sees a half-written file.

    A temp file in the same directory is fsynced, then moved over the target with
    ``os.replace``, retried briefly because on Windows antivirus or the indexer can hold the
    destination for a moment.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    for attempt in range(1, REPLACE_ATTEMPTS + 1):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(REPLACE_DELAY_SECONDS)
