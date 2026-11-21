"""FastAPI application factory and startup wiring (CLAUDE.md §4).

The backend owns users, auth, the admin account, uploads, the ``documents`` table
and chat-session ownership. It never imports AI code and never calls Ollama.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, get_settings
from app.db import get_sessionmaker
from app.routers import admin, admin_behaviour, auth, chat, health
from app.services.auth_service import seed_admin
from app.services.document_service import mark_interrupted_ingests

logger = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"


def configure_logging() -> None:
    """Use the ``logging`` module, never ``print`` (§14)."""
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)


def ensure_data_dirs(settings: Settings) -> None:
    """Create the runtime directories the backend owns (§5)."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    ensure_data_dirs(settings)
    # Absolute, because DATA_DIR is relative to the working directory (§11).
    logger.info("data dir   : %s", settings.data_dir)
    logger.info("uploads dir: %s", settings.uploads_dir)
    logger.info("ai service : %s", settings.ai_service_url)
    if insecure := settings.insecure_defaults:
        logger.warning(
            "still using placeholder values for %s - set real values in backend/.env",
            ", ".join(insecure),
        )
    with get_sessionmaker()() as db:
        seed_admin(db, settings)
        if interrupted := mark_interrupted_ingests(db):
            logger.warning("marked %d interrupted ingest(s) as failed", interrupted)
    yield


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()

    app = FastAPI(
        title="VeriDoc AI backend",
        description="Auth, users, admin documents and the chat gateway.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")
    app.include_router(admin.router, prefix="/api")
    app.include_router(admin_behaviour.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    return app


app = create_app()
