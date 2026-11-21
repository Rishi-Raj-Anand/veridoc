"""Needs a live Ollama with bge-m3 pulled: ``uv run pytest -m ollama``."""

from __future__ import annotations

import pytest

from ai_service.config import Settings
from ai_service.llm.ollama_client import OllamaClient

pytestmark = pytest.mark.ollama


def test_bge_m3_returns_1024_dim_vectors() -> None:
    vectors = OllamaClient(Settings(_env_file=None)).embed(["paracetamol for fever", "migraine"])

    assert len(vectors) == 2
    assert all(len(v) == 1024 for v in vectors)
