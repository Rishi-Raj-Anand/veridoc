"""Assistant behaviour contracts (CLAUDE.md §6.9, §9).

Mirrors the AI service's ``/internal/behaviour`` body, including the length caps, so a bad
edit is a 422 here before any internal call. The AI service validates again; keep the caps
in sync with ``ai/ai_service/behaviour.py``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

PERSONA_MAX_CHARS = 600
SCOPE_MAX_CHARS = 600
OUT_OF_SCOPE_MAX_CHARS = 400
GUARDRAILS_MAX_CHARS = 1500


class BehaviourSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    persona: str = Field(max_length=PERSONA_MAX_CHARS)
    scope: str = Field(min_length=1, max_length=SCOPE_MAX_CHARS)
    out_of_scope_message: str = Field(min_length=1, max_length=OUT_OF_SCOPE_MAX_CHARS)
    extra_guardrails: str = Field(max_length=GUARDRAILS_MAX_CHARS)


class BehaviourResponse(BaseModel):
    settings: BehaviourSettings
    defaults: BehaviourSettings
    locked_rules: list[str]
    updated_at: str | None
