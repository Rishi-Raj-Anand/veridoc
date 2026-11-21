"""Backend settings (CLAUDE.md §11).

This module is the only place in the backend that reads configuration. Never call
``os.getenv`` elsewhere (§14).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_SECRETS = frozenset({"change-me", "change-me-shared-secret"})


class Settings(BaseSettings):
    """Values from ``backend/.env``; see ``backend/.env.example``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    data_dir: Path = Path("../data")
    jwt_secret: str = "change-me"
    jwt_expire_minutes: int = 120
    admin_email: str = "admin@veridoc.local"
    admin_password: str = "change-me"
    ai_service_url: str = "http://127.0.0.1:8001"
    internal_api_key: str = "change-me-shared-secret"
    max_upload_mb: int = 25
    # How long the backend waits for one document to be indexed (§6.8: ~45 chunks/s with
    # bge-m3 on the target GPU, so a 22 MB CSV of ~25,000 chunks needs about 10-12 minutes).
    ingest_timeout_minutes: int = 60
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    @field_validator("data_dir")
    @classmethod
    def _resolve_data_dir(cls, value: Path) -> Path:
        """Resolve DATA_DIR once, at startup.

        §11 sets ``DATA_DIR=../data``, which is relative to the service directory,
        so the absolute path depends on the working directory. Resolving here (and
        logging it in ``main``) makes a wrong working directory visible immediately
        instead of silently creating a second data tree.
        """
        return value.expanduser().resolve()

    @field_validator("ingest_timeout_minutes")
    @classmethod
    def _check_ingest_timeout(cls, value: int) -> int:
        if not 1 <= value <= 1440:
            raise ValueError("INGEST_TIMEOUT_MINUTES must be between 1 and 1440")
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> list[str]:
        """Accept ``CORS_ORIGINS`` as a comma-separated string, not only JSON."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        if isinstance(value, list | tuple):
            return [str(origin).strip() for origin in value]
        raise TypeError("CORS_ORIGINS must be a comma-separated string or a list")

    @property
    def uploads_dir(self) -> Path:
        """Original admin-uploaded files (§5)."""
        return self.data_dir / "uploads"

    @property
    def db_path(self) -> Path:
        """Backend SQLite database (§5). Used from task 1.1 onwards."""
        return self.data_dir / "app.db"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def insecure_defaults(self) -> list[str]:
        """Names of secrets still left at their placeholder values."""
        return [
            name
            for name, value in (
                ("JWT_SECRET", self.jwt_secret),
                ("ADMIN_PASSWORD", self.admin_password),
                ("INTERNAL_API_KEY", self.internal_api_key),
            )
            if value in DEFAULT_SECRETS
        ]


@lru_cache
def get_settings() -> Settings:
    """Cached settings, suitable as a FastAPI dependency."""
    return Settings()
