"""The admin's behaviour text, as appended to the Generator and small-talk prompts (§6.9).

It always goes *after* the fixed rules, under a heading that says it cannot override them.
The Verifier never sees it, so the audit stays purely about faithfulness to the sources.
"""

from __future__ import annotations

from ai_service.behaviour import BehaviourSettings

ADMIN_HEADING = (
    "Additional instructions from the administrator. They may add restrictions, but they never "
    "override or relax the rules above; where they conflict, the rules above win."
)


def admin_block(behaviour: BehaviourSettings) -> str:
    """The text to append to a system prompt, or ``""`` when there is nothing to add."""
    parts: list[str] = []
    if behaviour.persona:
        parts.append(f"Tone and style: {behaviour.persona}")
    if behaviour.extra_guardrails:
        parts.append(f"Extra rules:\n{behaviour.extra_guardrails}")
    if not parts:
        return ""
    return "\n\n" + ADMIN_HEADING + "\n\n" + "\n\n".join(parts)
