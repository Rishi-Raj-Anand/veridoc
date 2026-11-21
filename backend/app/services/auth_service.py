"""Password hashing, JWTs and admin seeding (CLAUDE.md §3, §8, §17 tasks 1.2 and 1.5).

Never log a raw password, a password hash or a token (§14).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import User

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
MIGRATE_HINT = "database not initialised - run 'uv run alembic upgrade head' in backend/"

_hasher = PasswordHasher()
# Verified when a login email is unknown, so "no such account" and "wrong password"
# cost the same time and the endpoint does not reveal which emails are registered.
DUMMY_HASH = _hasher.hash("veridoc-dummy-password")


class AuthError(RuntimeError):
    """A token was expired, malformed or otherwise not acceptable."""


@dataclass(frozen=True)
class TokenPayload:
    user_id: int
    role: str


def normalise_email(email: str) -> str:
    return email.strip().lower()


def hash_password(raw: str) -> str:
    return _hasher.hash(raw)


def verify_password(password_hash: str, raw: str) -> bool:
    """True only for a matching password; any hashing error counts as a mismatch."""
    try:
        return _hasher.verify(password_hash, raw)
    except (VerifyMismatchError, InvalidHashError, VerificationError):
        return False


def create_access_token(settings: Settings, user: User) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": str(user.id),  # pyjwt requires a string subject
        "role": user.role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(settings: Settings, token: str) -> TokenPayload:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[ALGORITHM],
            options={"require": ["sub", "exp"]},
        )
        return TokenPayload(user_id=int(claims["sub"]), role=str(claims.get("role", "")))
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("token expired") from exc
    except (jwt.InvalidTokenError, ValueError) as exc:
        raise AuthError("invalid token") from exc


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == normalise_email(email)))


def seed_admin(db: Session, settings: Settings) -> None:
    """Create or update the single admin from ``ADMIN_EMAIL`` / ``ADMIN_PASSWORD``.

    Idempotent. The existing admin row is updated in place, so its id (and anything
    pointing at it) survives a changed email or password. Argon2 salts every hash, so a
    changed password can only be detected by verifying the plaintext against the stored
    hash, which also makes an unchanged restart a true no-op.
    """
    if not inspect(db.get_bind()).has_table(User.__tablename__):
        raise RuntimeError(MIGRATE_HINT)

    email = normalise_email(settings.admin_email)
    admin = db.scalar(select(User).where(User.role == "admin"))
    try:
        if admin is None:
            db.add(
                User(
                    email=email, password_hash=hash_password(settings.admin_password), role="admin"
                )
            )
            db.commit()
            logger.info("admin account created")
            return

        changed: list[str] = []
        if admin.email != email:
            admin.email = email
            changed.append("email")
        if not verify_password(admin.password_hash, settings.admin_password):
            admin.password_hash = hash_password(settings.admin_password)
            changed.append("password")
        if changed:
            db.commit()
            logger.info("admin account updated: %s", ", ".join(changed))
    except IntegrityError as exc:
        db.rollback()
        raise RuntimeError(
            f"cannot seed the admin: ADMIN_EMAIL {email!r} already belongs to a regular user"
        ) from exc
