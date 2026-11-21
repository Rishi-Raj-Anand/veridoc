"""``GET/PUT /internal/behaviour`` (CLAUDE.md §6.9, §9): the admin-editable assistant behaviour.

The backend proxies these for the admin. Changes apply from the next chat message.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ai_service.behaviour import BehaviourSettings, StoredBehaviour
from ai_service.config import LOCKED_RULES
from ai_service.deps import BehaviourStoreDep
from ai_service.memory.chat_store import now_iso
from ai_service.security import require_internal_key

router = APIRouter(
    prefix="/internal",
    tags=["behaviour"],
    dependencies=[Depends(require_internal_key)],
)


class BehaviourResponse(BaseModel):
    settings: BehaviourSettings
    defaults: BehaviourSettings
    locked_rules: list[str]
    updated_at: str | None


def _response(stored: StoredBehaviour) -> BehaviourResponse:
    return BehaviourResponse(
        settings=stored.settings,
        defaults=BehaviourSettings(),
        locked_rules=list(LOCKED_RULES),
        updated_at=stored.updated_at,
    )


@router.get("/behaviour", response_model=BehaviourResponse, summary="Current assistant behaviour")
async def get_behaviour(store: BehaviourStoreDep) -> BehaviourResponse:
    return _response(store.stored())


@router.put("/behaviour", response_model=BehaviourResponse, summary="Replace assistant behaviour")
async def put_behaviour(body: BehaviourSettings, store: BehaviourStoreDep) -> BehaviourResponse:
    stored = await asyncio.to_thread(store.update, body, now_iso())
    return _response(stored)
