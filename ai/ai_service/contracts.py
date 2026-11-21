"""Small shared literals for the chat contract (CLAUDE.md §6.9, §9)."""

from __future__ import annotations

from typing import Literal

# What kind of message the user sent; decided by ``classify_intent`` (§6.9).
Intent = Literal["medical", "smalltalk", "out_of_scope"]

# ``ChatResponse.status``. Only ``verified`` and ``fallback`` come from the medical RAG loop.
ChatStatus = Literal["verified", "fallback", "smalltalk", "out_of_scope"]
