"""Chat gateway (CLAUDE.md §9). Any signed-in user may chat; sessions are private to one user."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from starlette.concurrency import run_in_threadpool

from app.config import Settings, get_settings
from app.deps import CurrentUser, DbSession
from app.schemas.chat import ChatMessageRequest, ChatResponse
from app.services import ai_client, chat_service
from app.services.ai_client import AiServiceError
from app.services.chat_service import SessionForbidden

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

AI_UNAVAILABLE = "Doctor AI is unavailable right now. Please try again in a moment."
MODEL_OFFLINE = "Doctor AI's language model is offline right now. Please try again later."
TOO_SLOW = "Doctor AI took too long to answer. Please try again."

# What a user sees for each ``AiServiceError.code`` (Phase 8.1). Users get a short, plain
# sentence; the technical reason is logged and shown to the admin on the status card.
USER_MESSAGES: dict[str, str] = {
    "ai_unreachable": AI_UNAVAILABLE,
    "ollama_unreachable": MODEL_OFFLINE,
    "model_missing": MODEL_OFFLINE,
    "ollama_timeout": TOO_SLOW,
    "ai_timeout": TOO_SLOW,
}


def user_message(exc: AiServiceError) -> str:
    return USER_MESSAGES.get(exc.code, AI_UNAVAILABLE)


@router.post("/message", response_model=ChatResponse, summary="Send one message to Doctor AI")
async def send_message(
    body: ChatMessageRequest,
    db: DbSession,
    user: CurrentUser,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ChatResponse:
    try:
        await run_in_threadpool(chat_service.claim_session, db, body.session_id, user)
    except SessionForbidden as exc:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="this chat session belongs to another user"
        ) from exc
    try:
        return await ai_client.send_chat(settings, body.session_id, user.id, body.message)
    except AiServiceError as exc:
        logger.warning("chat turn failed for session %s (%s): %s", body.session_id, exc.code, exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=user_message(exc)) from exc
