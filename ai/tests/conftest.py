"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from ai_service.config import get_settings
from tests.helpers import INTERNAL_KEY


@pytest.fixture(autouse=True)
def test_environment(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[None]:
    """Isolate settings: a known internal key and a throwaway DATA_DIR.

    ``get_settings`` is cached, so the cache is cleared around every test.
    """
    monkeypatch.setenv("INTERNAL_API_KEY", INTERNAL_KEY)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
