"""Chat-session ownership (CLAUDE.md §6.7): the first message claims a session id."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ChatSession, User


class SessionForbidden(Exception):
    """The session id already belongs to another user."""


def claim_session(db: Session, session_id: UUID, user: User) -> None:
    """Record ``(session_id, user_id)`` on first use; reject a session owned by someone else."""
    key = str(session_id)
    existing = db.get(ChatSession, key)
    if existing is None:
        db.add(ChatSession(id=key, user_id=user.id))
        try:
            db.commit()
            return
        except IntegrityError:  # a concurrent first message won the insert
            db.rollback()
            existing = db.get(ChatSession, key)
    if existing is None or existing.user_id != user.id:
        raise SessionForbidden(key)
