"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import Base, get_engine, get_sessionmaker, reset_engine
from tests.helpers import ADMIN_EMAIL, ADMIN_PASSWORD


@pytest.fixture(autouse=True)
def test_environment(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[None]:
    """Isolate every test: throwaway DATA_DIR, known secrets, fresh caches.

    Without this a DB-touching test would write to the developer's real
    ``data/app.db``. ``get_settings`` and the engine are cached, so both are reset
    around each test.
    """
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JWT_SECRET", "a-test-secret-that-is-at-least-32-bytes-long")
    monkeypatch.setenv("ADMIN_EMAIL", ADMIN_EMAIL)
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    get_settings.cache_clear()
    reset_engine()
    yield
    reset_engine()
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture
def db_tables(settings: Settings) -> Engine:
    """Create the schema straight from the models (migrations have their own test)."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = get_engine()
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db(db_tables: Engine) -> Iterator[Session]:
    with get_sessionmaker()() as session:
        yield session
