"""Register, login and current-user endpoints (CLAUDE.md §9)."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError

from app.config import Settings, get_settings
from app.deps import CurrentUser, DbSession
from app.models import User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from app.services import auth_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

EMAIL_TAKEN = "an account with this email already exists"


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a user account",
)
def register(
    body: RegisterRequest,
    db: DbSession,
    settings: Annotated[Settings, Depends(get_settings)],
) -> User:
    email = auth_service.normalise_email(body.email)
    # The admin address is reserved: a user must not be able to squat it.
    if email == auth_service.normalise_email(settings.admin_email):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=EMAIL_TAKEN)
    if auth_service.get_user_by_email(db, email) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=EMAIL_TAKEN)

    user = User(email=email, password_hash=auth_service.hash_password(body.password), role="user")
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:  # lost a race against a concurrent registration
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=EMAIL_TAKEN) from exc
    logger.info("registered user %s", email)
    return user


@router.post("/login", response_model=TokenResponse, summary="Exchange credentials for a JWT")
def login(
    body: LoginRequest,
    db: DbSession,
    settings: Annotated[Settings, Depends(get_settings)],
) -> TokenResponse:
    user = auth_service.get_user_by_email(db, body.email)
    # Always verify against *some* hash so both failure modes take the same time.
    password_hash = user.password_hash if user else auth_service.DUMMY_HASH
    valid = auth_service.verify_password(password_hash, body.password)
    if user is None or not valid:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    logger.info("login %s", user.email)
    return TokenResponse(
        access_token=auth_service.create_access_token(settings, user), role=user.role
    )


@router.get("/me", response_model=UserResponse, summary="The authenticated user")
def me(user: CurrentUser) -> User:
    return user
