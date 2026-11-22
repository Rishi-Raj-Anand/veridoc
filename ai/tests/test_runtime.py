"""Shared wiring for the server and the evaluation (CLAUDE.md §13) and the Qdrant lock."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from ai_service import runtime
from ai_service.config import get_settings
from ai_service.graph.pipeline import build_pipeline
from ai_service.runtime import QdrantOpenError, build_runtime, open_qdrant_sync

AI_SERVICE = Path(__file__).resolve().parents[1] / "ai_service"


def test_a_second_open_of_the_same_storage_fails_with_the_lock_hint() -> None:
    settings = get_settings()
    settings.vectors_dir.mkdir(parents=True)
    first = open_qdrant_sync(settings)
    try:
        with pytest.raises(QdrantOpenError, match="one process only"):
            open_qdrant_sync(settings)
    finally:
        first.close()


def test_build_runtime_wires_a_pipeline_for_both_variants() -> None:
    settings = get_settings()
    client = QdrantClient(":memory:")

    full = build_runtime(settings, client)
    single = build_runtime(
        settings.model_copy(update={"max_iterations": 1}), client, verify_drafts=False
    )

    assert full.pipeline is not single.pipeline
    assert full.vector_store.count() == 0
    assert full.docstore.filenames() == set()


def test_build_runtime_closes_the_client_when_wiring_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Client:
        closed = False

        def close(self) -> None:
            self.closed = True

    def boom(self: object) -> None:
        raise RuntimeError("collection has the wrong vector size")

    monkeypatch.setattr(runtime.VectorStore, "ensure_collection", boom)
    client = Client()

    with pytest.raises(RuntimeError, match="wrong vector size"):
        build_runtime(get_settings(), client)  # type: ignore[arg-type]
    assert client.closed


def test_the_verifier_is_on_by_default_everywhere() -> None:
    for fn in (build_pipeline, build_runtime):
        assert inspect.signature(fn).parameters["verify_drafts"].default is True


def test_the_server_never_builds_the_unverified_ablation() -> None:
    """§7: only the offline evaluation may skip the Verifier; users always get verified text."""
    server_files = [AI_SERVICE / "main.py", *sorted((AI_SERVICE / "routers").glob("*.py"))]
    for path in server_files:
        assert "verify_drafts" not in path.read_text(encoding="utf-8"), path.name
    call = re.compile(r"verify_drafts=False\s*[,)]")  # a call, not a docstring mention
    users = [
        p.relative_to(AI_SERVICE).as_posix()
        for p in AI_SERVICE.rglob("*.py")
        if call.search(p.read_text(encoding="utf-8"))
    ]
    assert users == ["eval/run_ragas.py"]
