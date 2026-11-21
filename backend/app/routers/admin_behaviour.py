"""Admin: the assistant's behaviour and guardrails (CLAUDE.md §6.9, §9). Admin only.

The AI service owns the prompts, so these routes proxy ``/internal/behaviour``. Edits apply
from the next chat message. The §7 safety rules are returned read-only and cannot be edited.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.config import Settings, get_settings
from app.deps import AdminUser
from app.schemas.behaviour import BehaviourResponse, BehaviourSettings
from app.services import ai_client
from app.services.ai_client import AiServiceError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/behaviour", tags=["admin"])

AI_UNAVAILABLE_LOAD = (
    "The AI service is unavailable, so the assistant behaviour could not be loaded. "
    "Make sure it is running and try again."
)
AI_UNAVAILABLE_SAVE = (
    "The AI service is unavailable, so nothing was saved. Make sure it is running and try again."
)


@router.get("", response_model=BehaviourResponse, summary="Current assistant behaviour")
async def get_behaviour(
    settings: Annotated[Settings, Depends(get_settings)], _admin: AdminUser
) -> BehaviourResponse:
    try:
        return await ai_client.get_behaviour(settings)
    except AiServiceError as exc:
        logger.warning("could not load the assistant behaviour: %s", exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail=AI_UNAVAILABLE_LOAD
        ) from exc


@router.put("", response_model=BehaviourResponse, summary="Update assistant behaviour")
async def put_behaviour(
    body: BehaviourSettings,
    settings: Annotated[Settings, Depends(get_settings)],
    admin: AdminUser,
) -> BehaviourResponse:
    try:
        result = await ai_client.update_behaviour(settings, body)
    except AiServiceError as exc:
        logger.warning("could not save the assistant behaviour: %s", exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail=AI_UNAVAILABLE_SAVE
        ) from exc
    logger.info("assistant behaviour updated by %s", admin.email)
    return result
