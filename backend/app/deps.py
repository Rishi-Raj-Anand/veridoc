"""Shared FastAPI dependencies (CLAUDE.md §17 task 1.4).

Credentials are declared optional so a missing token is a 401, not FastAPI's 403.
The caller's role is read off the freshly loaded ``users`` row, never trusted from the
token, so a deleted account or changed role takes effect immediately.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_sessionmaker
from app.models import User
from app.services.auth_service import AuthError, decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)


def get_db() -> Iterator[Session]:
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()


DbSession = Annotated[Session, Depends(get_db)]


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    settings: Annotated[Settings, Depends(get_settings)],
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> User:
    if credentials is None:
        raise _unauthorized("not authenticated")
    try:
        payload = decode_access_token(settings, credentials.credentials)
    except AuthError as exc:
        raise _unauthorized(str(exc)) from exc
    user = db.get(User, payload.user_id)
    if user is None:
        raise _unauthorized("invalid token")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="admin privileges required")
    return user


AdminUser = Annotated[User, Depends(require_admin)]
