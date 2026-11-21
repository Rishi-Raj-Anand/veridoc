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
        logger.warning("chat turn failed for session %s: %s", body.session_id, exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=AI_UNAVAILABLE) from exc
