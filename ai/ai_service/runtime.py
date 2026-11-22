"""Build the AI service's components once, the same way for the server and the evaluation.

``main.lifespan`` and ``eval/run_ragas.py`` both call :func:`build_runtime`, so the RAGAS run
measures exactly the pipeline that ships (CLAUDE.md §13). Everything here is synchronous; the
server runs it in a worker thread.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from langgraph.graph.state import CompiledStateGraph
from qdrant_client import QdrantClient

from ai_service.agents.classifier import IntentClassifier
from ai_service.agents.condenser import Condenser
from ai_service.agents.generator import Generator
from ai_service.agents.safety import SafetyChecker
from ai_service.agents.smalltalk import SmallTalk
from ai_service.agents.verifier import Verifier
from ai_service.behaviour import BehaviourStore
from ai_service.config import Settings
from ai_service.graph.pipeline import build_pipeline
from ai_service.llm.ollama_client import OllamaClient
from ai_service.memory.chat_store import ChatStore
from ai_service.retrieval.docstore import Docstore
from ai_service.retrieval.retriever import Retriever
from ai_service.retrieval.vector_store import VectorStore

logger = logging.getLogger(__name__)

QDRANT_LOCK_HINT = (
    "Qdrant local mode allows one process only: make sure no other AI service "
    "instance or eval run holds it, and never start this service with --reload "
    "(CLAUDE.md §6.8)."
)


class QdrantOpenError(RuntimeError):
    """The embedded Qdrant storage could not be opened (usually: another process holds it)."""


@dataclass
class Runtime:
    llm: OllamaClient
    vector_store: VectorStore
    docstore: Docstore
    behaviour_store: BehaviourStore
    pipeline: CompiledStateGraph
    chat_store: ChatStore


def open_qdrant_sync(settings: Settings) -> QdrantClient:
    """Open the one embedded Qdrant client this process is allowed to hold (§4)."""
    try:
        return QdrantClient(path=str(settings.vectors_dir))
    except Exception as exc:
        message = f"could not open Qdrant local storage at {settings.vectors_dir}: {exc}"
        raise QdrantOpenError(f"{message}. {QDRANT_LOCK_HINT}") from exc


def build_runtime(
    settings: Settings,
    client: QdrantClient,
    *,
    verify_drafts: bool = True,
) -> Runtime:
    """Wire the stores, agents and LangGraph pipeline on top of an open Qdrant client.

    ``verify_drafts=False`` builds the single-pass ablation (no Verifier) for the offline
    evaluation only; the server never passes it (a test enforces this). On failure the client
    is closed, because the caller has nothing else to clean up.
    """
    try:
        store = VectorStore(client, settings)
        store.ensure_collection()
        docstore = Docstore(settings.docstore_path)
        llm = OllamaClient(settings)
        behaviour_store = BehaviourStore(settings.behaviour_path)
        # Each loads its prompt / terms file now and fails loudly if it is absent or invalid.
        generator = Generator(llm, settings, behaviour_store)
        verifier = Verifier(llm, settings)
        condenser = Condenser(llm, settings)
        safety = SafetyChecker.load()
        pipeline = build_pipeline(
            Retriever(store, docstore, llm, settings),
            generator,
            verifier,
            condenser,
            safety,
            settings,
            classifier=IntentClassifier(llm, settings),
            smalltalk=SmallTalk(llm, settings, behaviour_store),
            behaviour=behaviour_store,
            verify_drafts=verify_drafts,
        )
        chat_store = ChatStore(settings.chats_dir)
    except Exception:
        client.close()
        raise
    return Runtime(
        llm=llm,
        vector_store=store,
        docstore=docstore,
        behaviour_store=behaviour_store,
        pipeline=pipeline,
        chat_store=chat_store,
    )
