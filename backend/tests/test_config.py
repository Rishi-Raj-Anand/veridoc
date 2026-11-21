"""Settings behaviour (CLAUDE.md §11)."""

from __future__ import annotations

import pytest

from app.config import Settings


def test_cors_origins_accepts_comma_separated_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """``CORS_ORIGINS`` in .env is a bare list, not JSON."""
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173, http://127.0.0.1:5173")
    settings = Settings(_env_file=None)
    assert settings.cors_origins == ["http://localhost:5173", "http://127.0.0.1:5173"]


def test_cors_origins_single_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173")
    assert Settings(_env_file=None).cors_origins == ["http://localhost:5173"]


def test_data_dir_is_resolved_to_an_absolute_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", "../data")
    settings = Settings(_env_file=None)
    assert settings.data_dir.is_absolute()
    assert settings.data_dir.name == "data"


def test_derived_paths(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    settings = Settings(_env_file=None)
    assert settings.uploads_dir == settings.data_dir / "uploads"
    assert settings.db_path == settings.data_dir / "app.db"


def test_max_upload_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_UPLOAD_MB", "25")
    assert Settings(_env_file=None).max_upload_bytes == 25 * 1024 * 1024


def test_insecure_defaults_are_reported() -> None:
    settings = Settings(_env_file=None)
    assert set(settings.insecure_defaults) == {"JWT_SECRET", "ADMIN_PASSWORD", "INTERNAL_API_KEY"}


def test_no_insecure_defaults_once_configured() -> None:
    settings = Settings(
        _env_file=None,
        jwt_secret="a-real-secret",
        admin_password="a-real-password",
        internal_api_key="a-real-key",
    )
    assert settings.insecure_defaults == []
