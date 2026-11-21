"""``POST /internal/chat`` (CLAUDE.md §9): run one turn through the pipeline."""

from __future__ import annotations

import asyncio
import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from ai_service.config import DISCLAIMER
from ai_service.deps import PipelineDep
from ai_service.graph.pipeline import run_turn
from ai_service.llm.ollama_client import OllamaError
from ai_service.security import require_internal_key

logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 2000

router = APIRouter(
    prefix="/internal",
    tags=["chat"],
    dependencies=[Depends(require_internal_key)],
)


class ChatRequest(BaseModel):
    session_id: UUID
    user_id: int
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be blank")
        return value


class Citation(BaseModel):
    n: int
    filename: str
    page: int


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation]
    status: Literal["verified", "fallback"]
    emergency: bool
    disclaimer: str


def _lock(request: Request) -> asyncio.Lock:
    lock = getattr(request.app.state, "chat_lock", None)
    if lock is None:
        lock = request.app.state.chat_lock = asyncio.Lock()
    return lock


@router.post("/chat", response_model=ChatResponse, summary="Answer one chat message")
async def chat(body: ChatRequest, request: Request, pipeline: PipelineDep) -> ChatResponse:
    try:
        # One model in VRAM: parallel turns would only make Ollama swap models (§6.8).
        async with _lock(request):
            result = await asyncio.to_thread(
                run_turn,
                pipeline,
                session_id=str(body.session_id),
                user_id=body.user_id,
                message=body.message,
            )
    except OllamaError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("chat turn failed for session %s", body.session_id)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, detail="chat turn failed unexpectedly"
        ) from exc
    return ChatResponse(
        answer=result.answer,
        citations=[Citation(**c) for c in result.citations],
        status=result.status,
        emergency=result.emergency,
        disclaimer=DISCLAIMER,
    )
