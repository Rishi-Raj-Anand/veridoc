"""AI service settings (CLAUDE.md §11).

This module is the only place in the AI service that reads configuration. Model
names, thresholds and Top-K values live here, never hardcoded in logic (§3).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_INTERNAL_KEY = "change-me-shared-secret"

# User-facing medical text (CLAUDE.md §7.4, §7.5). Defined here and nowhere else; the
# pipeline and the API both read these constants.
FALLBACK_MESSAGE = (
    "I couldn't find verified information in my medical references to answer this safely. "
    "Please consult a qualified doctor."
)
EMERGENCY_NOTICE = (
    "⚠️ This may be a medical emergency. Call **112** (or **108** for an ambulance in "
    "India) or go to the nearest emergency department now."
)
DISCLAIMER = (
    "VeriDoc AI provides information from reference documents and is not a substitute for "
    "professional medical advice, diagnosis, or treatment."
)

# The §7 rules in plain words. Always in the prompts, never editable: the admin's behaviour
# text (§6.9) can only add restrictions on top of these. Shown read-only on the admin page.
LOCKED_RULES: tuple[str, ...] = (
    "Medical answers come only from the uploaded documents, with a citation for every claim.",
    "Every draft is audited by the Verifier; if it cannot be verified after 3 rounds, the "
    "fixed fallback message is returned instead of a guess.",
    "Red-flag emergencies always get the call-112/108 notice first and are never treated as "
    "small talk or declined.",
    "Doses, children, pregnancy, breastfeeding and drug interactions are stated only when a "
    "document states them explicitly; otherwise the user is told to ask a doctor or pharmacist.",
    "Answers are never presented as a diagnosis.",
    "Small-talk replies never contain medical information.",
    "The disclaimer is shown on every reply.",
)


class Settings(BaseSettings):
    """Values from ``ai/.env``; see ``ai/.env.example``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    data_dir: Path = Path("../data")
    internal_api_key: str = DEFAULT_INTERNAL_KEY
    ollama_host: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen3:8b"
    embed_model: str = "bge-m3"
    num_ctx: int = 8192
    faithfulness_threshold: float = 0.85
    relevance_threshold: float = 0.75
    min_retrieval_score: float = 0.35
    max_iterations: int = 3
    history_max_messages: int = 6
    top_k_precision: int = 3  # iteration-1 retrieval (§6.3)
    top_k_level2: int = 8  # iteration-2 expansion: children fetched, then widened to parents
    max_parents_level2: int = 4
    top_k_level3: int = 15  # iteration-3 expansion
    max_parents_level3: int = 6
    # Prompt budget (§6.4); the system prompt is never trimmed.
    context_max_tokens: int = 4800
    history_max_tokens: int = 1500
    # Chunking and embedding shape (§6.1). Changing any of these means re-ingesting
    # every document: existing vectors and parents were built with the old values.
    parent_chunk_tokens: int = 800
    child_chunk_tokens: int = 300
    child_chunk_overlap_tokens: int = 50
    embed_dim: int = 1024

    @field_validator("max_iterations")
    @classmethod
    def _cap_iterations(cls, value: int) -> int:
        """§6.3 defines exactly three escalation levels, so the cap is 1..3."""
        if not 1 <= value <= 3:
            raise ValueError("MAX_ITERATIONS must be between 1 and 3 (CLAUDE.md §6.3)")
        return value

    @field_validator("data_dir")
    @classmethod
    def _resolve_data_dir(cls, value: Path) -> Path:
        """Resolve DATA_DIR once, at startup.

        §11 sets ``DATA_DIR=../data``, relative to the service directory, so the
        absolute path depends on the working directory. ``main`` logs the resolved
        value to make a wrong working directory obvious immediately.
        """
        return value.expanduser().resolve()

    @property
    def uploads_dir(self) -> Path:
        """Original admin-uploaded files, written by the backend (§5)."""
        return self.data_dir / "uploads"

    @property
    def vectors_dir(self) -> Path:
        """Qdrant local storage. Only this process may open it (§4)."""
        return self.data_dir / "vectors"

    @property
    def docstore_path(self) -> Path:
        """Parent-chunk store (§6.1.6). Used from task 2.4 onwards."""
        return self.data_dir / "docstore.db"

    @property
    def chats_dir(self) -> Path:
        """Chat transcripts (§6.7). Used from task 5.1 onwards."""
        return self.data_dir / "chats"

    @property
    def behaviour_path(self) -> Path:
        """Admin-editable assistant behaviour (§6.9)."""
        return self.data_dir / "behaviour.json"

    @property
    def eval_dir(self) -> Path:
        """RAGAS output (§13). Used from task 7.2 onwards."""
        return self.data_dir / "eval"


@lru_cache
def get_settings() -> Settings:
    """Cached settings, suitable as a FastAPI dependency."""
    return Settings()
