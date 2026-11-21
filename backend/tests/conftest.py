"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Iterator[None]:
    """``get_settings`` is cached, and tests change the environment around it."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
