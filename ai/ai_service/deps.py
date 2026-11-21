"""Shared dependencies exposing the objects ``lifespan`` builds (CLAUDE.md §4)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from langgraph.graph.state import CompiledStateGraph

from ai_service.llm.ollama_client import OllamaClient
from ai_service.memory.chat_store import ChatStore
from ai_service.retrieval.docstore import Docstore
from ai_service.retrieval.vector_store import VectorStore


def _from_state[T](request: Request, name: str, label: str) -> T:
    value = getattr(request.app.state, name, None)
    if value is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"{label} is not available")
    return value


def get_vector_store(request: Request) -> VectorStore:
    return _from_state(request, "vector_store", "vector store")


def get_docstore(request: Request) -> Docstore:
    return _from_state(request, "docstore", "docstore")


def get_llm(request: Request) -> OllamaClient:
    return _from_state(request, "llm", "Ollama client")


def get_pipeline(request: Request) -> CompiledStateGraph:
    return _from_state(request, "pipeline", "chat pipeline")


def get_chat_store(request: Request) -> ChatStore:
    return _from_state(request, "chat_store", "chat store")


VectorStoreDep = Annotated[VectorStore, Depends(get_vector_store)]
DocstoreDep = Annotated[Docstore, Depends(get_docstore)]
LlmDep = Annotated[OllamaClient, Depends(get_llm)]
PipelineDep = Annotated[CompiledStateGraph, Depends(get_pipeline)]
ChatStoreDep = Annotated[ChatStore, Depends(get_chat_store)]
