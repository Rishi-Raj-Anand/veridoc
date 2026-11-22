"""AI service application factory and startup wiring (CLAUDE.md §4).

The AI service owns parsing, chunking, embeddings, Qdrant, the parent-chunk
docstore, the LangGraph pipeline, chat transcripts and evaluation. It never touches
``app.db`` and has no user auth.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ai_service.config import DEFAULT_INTERNAL_KEY, Settings, get_settings
from ai_service.routers import behaviour, chat, health, ingest
from ai_service.runtime import build_runtime, open_qdrant_sync

logger = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"


def configure_logging() -> None:
    """Use the ``logging`` module, never ``print`` (§14)."""
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)


def ensure_data_dirs(settings: Settings) -> None:
    """Create the runtime directories the AI service owns (§5)."""
    for directory in (
        settings.data_dir,
        settings.vectors_dir,
        settings.chats_dir,
        settings.eval_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    ensure_data_dirs(settings)
    # Absolute, because DATA_DIR is relative to the working directory (§11).
    logger.info("data dir   : %s", settings.data_dir)
    logger.info("vectors dir: %s", settings.vectors_dir)
    logger.info("ollama host: %s", settings.ollama_host)
    logger.info("models     : llm=%s embed=%s", settings.llm_model, settings.embed_model)
    if settings.internal_api_key == DEFAULT_INTERNAL_KEY:
        logger.warning(
            "INTERNAL_API_KEY is still the placeholder value - set a real shared secret "
            "in both ai/.env and backend/.env"
        )

    client = await asyncio.to_thread(open_qdrant_sync, settings)
    app.state.qdrant = client
    logger.info("qdrant local storage open")
    # Shared with the evaluation script, so RAGAS measures the pipeline that ships (§13).
    # build_runtime closes the client itself if it fails.
    runtime = await asyncio.to_thread(build_runtime, settings, client)
    app.state.vector_store = runtime.vector_store
    app.state.docstore = runtime.docstore
    app.state.llm = runtime.llm
    app.state.behaviour_store = runtime.behaviour_store
    app.state.pipeline = runtime.pipeline
    app.state.chat_store = runtime.chat_store
    logger.info("docstore     : %s", settings.docstore_path)
    try:
        yield
    finally:
        await asyncio.to_thread(client.close)
        app.state.qdrant = None
        app.state.vector_store = None
        app.state.pipeline = None
        app.state.chat_store = None
        logger.info("qdrant local storage closed")


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="VeriDoc AI service",
        description="Internal only. Every route requires the X-Internal-Key header.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.include_router(ingest.router)
    app.include_router(chat.router)
    app.include_router(behaviour.router)
    app.state.ingest_lock = asyncio.Lock()
    app.state.chat_lock = asyncio.Lock()
    return app


app = create_app()
