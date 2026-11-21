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
from qdrant_client import QdrantClient

from ai_service.agents.classifier import IntentClassifier
from ai_service.agents.condenser import Condenser
from ai_service.agents.generator import Generator
from ai_service.agents.safety import SafetyChecker
from ai_service.agents.smalltalk import SmallTalk
from ai_service.agents.verifier import Verifier
from ai_service.behaviour import BehaviourStore
from ai_service.config import DEFAULT_INTERNAL_KEY, Settings, get_settings
from ai_service.graph.pipeline import build_pipeline
from ai_service.llm.ollama_client import OllamaClient
from ai_service.memory.chat_store import ChatStore
from ai_service.retrieval.docstore import Docstore
from ai_service.retrieval.retriever import Retriever
from ai_service.retrieval.vector_store import VectorStore
from ai_service.routers import behaviour, chat, health, ingest

logger = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"

QDRANT_LOCK_HINT = (
    "Qdrant local mode allows one process only: make sure no other AI service "
    "instance or eval run holds it, and never start this service with --reload "
    "(CLAUDE.md §6.8)."
)


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


async def open_qdrant(settings: Settings) -> QdrantClient:
    """Open the one embedded Qdrant client this process is allowed to hold (§4)."""
    try:
        return await asyncio.to_thread(QdrantClient, path=str(settings.vectors_dir))
    except Exception as exc:
        message = f"could not open Qdrant local storage at {settings.vectors_dir}: {exc}"
        raise RuntimeError(f"{message}. {QDRANT_LOCK_HINT}") from exc


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

    client = await open_qdrant(settings)
    app.state.qdrant = client
    logger.info("qdrant local storage open")
    store = VectorStore(client, settings)
    try:
        await asyncio.to_thread(store.ensure_collection)
    except Exception:
        await asyncio.to_thread(client.close)
        raise
    app.state.vector_store = store
    docstore = Docstore(settings.docstore_path)
    app.state.docstore = docstore
    llm = OllamaClient(settings)
    app.state.llm = llm
    try:
        behaviour_store = BehaviourStore(settings.behaviour_path)
        app.state.behaviour_store = behaviour_store
        # Each loads its prompt / terms file now and fails loudly if it is absent or invalid.
        generator = Generator(llm, settings, behaviour_store)
        verifier = Verifier(llm, settings)
        condenser = Condenser(llm, settings)
        safety = SafetyChecker.load()
        app.state.pipeline = build_pipeline(
            Retriever(store, docstore, llm, settings),
            generator,
            verifier,
            condenser,
            safety,
            settings,
            classifier=IntentClassifier(llm, settings),
            smalltalk=SmallTalk(llm, settings, behaviour_store),
            behaviour=behaviour_store,
        )
        app.state.chat_store = ChatStore(settings.chats_dir)
    except Exception:
        await asyncio.to_thread(client.close)
        raise
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
