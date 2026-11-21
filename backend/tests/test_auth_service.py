"""Hashing, JWTs and admin seeding (CLAUDE.md §17 tasks 1.2 and 1.5)."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import User
from app.services import auth_service
from app.services.auth_service import AuthError
from tests.helpers import ADMIN_EMAIL, ADMIN_PASSWORD

SECRET = "a-test-secret-that-is-at-least-32-bytes-long"


def make_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def admin_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User).where(User.role == "admin")) or 0


def test_hash_and_verify_round_trip() -> None:
    hashed = auth_service.hash_password("correct horse")
    assert hashed != "correct horse"
    assert auth_service.verify_password(hashed, "correct horse")
    assert not auth_service.verify_password(hashed, "wrong")


def test_verify_treats_a_garbage_hash_as_a_mismatch() -> None:
    assert auth_service.verify_password("not-a-hash", "anything") is False


def test_token_round_trip_carries_subject_and_role() -> None:
    settings = make_settings(jwt_secret=SECRET)
    token = auth_service.create_access_token(settings, User(id=7, email="a@b.c", role="admin"))

    payload = auth_service.decode_access_token(settings, token)

    assert (payload.user_id, payload.role) == (7, "admin")


def test_expired_token_is_rejected() -> None:
    settings = make_settings(jwt_secret=SECRET, jwt_expire_minutes=-1)
    token = auth_service.create_access_token(settings, User(id=1, email="a@b.c", role="user"))

    with pytest.raises(AuthError, match="expired"):
        auth_service.decode_access_token(settings, token)


def test_tampered_or_foreign_token_is_rejected() -> None:
    token = auth_service.create_access_token(
        make_settings(jwt_secret=SECRET + "-one"), User(id=1, email="a@b.c", role="user")
    )
    with pytest.raises(AuthError, match="invalid"):
        auth_service.decode_access_token(make_settings(jwt_secret=SECRET + "-two"), token)
    with pytest.raises(AuthError, match="invalid"):
        auth_service.decode_access_token(make_settings(jwt_secret=SECRET + "-one"), token + "x")


def test_seed_creates_the_admin_once(db: Session, settings: Settings) -> None:
    auth_service.seed_admin(db, settings)
    admin = db.scalar(select(User).where(User.role == "admin"))

    assert admin is not None
    assert admin.email == ADMIN_EMAIL
    assert auth_service.verify_password(admin.password_hash, ADMIN_PASSWORD)


def test_seed_is_idempotent(db: Session, settings: Settings) -> None:
    auth_service.seed_admin(db, settings)
    first = db.scalar(select(User).where(User.role == "admin"))
    assert first is not None
    first_id, first_hash = first.id, first.password_hash

    auth_service.seed_admin(db, settings)
    db.expire_all()
    second = db.scalar(select(User).where(User.role == "admin"))

    assert second is not None
    assert (second.id, second.password_hash) == (first_id, first_hash)
    assert admin_count(db) == 1


def test_seed_updates_the_password_in_place(db: Session, settings: Settings) -> None:
    auth_service.seed_admin(db, settings)
    original = db.scalar(select(User).where(User.role == "admin"))
    assert original is not None
    original_id = original.id

    auth_service.seed_admin(db, make_settings(admin_password="a-new-admin-password"))
    db.expire_all()
    admin = db.scalar(select(User).where(User.role == "admin"))

    assert admin is not None
    assert admin.id == original_id
    assert auth_service.verify_password(admin.password_hash, "a-new-admin-password")
    assert admin_count(db) == 1


def test_seed_updates_the_email_in_place(db: Session, settings: Settings) -> None:
    auth_service.seed_admin(db, settings)
    original = db.scalar(select(User).where(User.role == "admin"))
    assert original is not None
    original_id = original.id

    auth_service.seed_admin(db, make_settings(admin_email="Boss@Test.local"))
    db.expire_all()
    admin = db.scalar(select(User).where(User.role == "admin"))

    assert admin is not None
    assert (admin.id, admin.email) == (original_id, "boss@test.local")
    assert admin_count(db) == 1


def test_seed_refuses_an_email_owned_by_a_regular_user(db: Session, settings: Settings) -> None:
    auth_service.seed_admin(db, settings)
    db.add(User(email="taken@test.local", password_hash="x", role="user"))
    db.commit()

    with pytest.raises(RuntimeError, match="already belongs to a regular user"):
        auth_service.seed_admin(db, make_settings(admin_email="taken@test.local"))


def test_seed_on_an_uninitialised_database_names_the_fix(settings: Settings) -> None:
    from app.db import get_sessionmaker

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with get_sessionmaker()() as db, pytest.raises(RuntimeError, match="alembic upgrade head"):
        auth_service.seed_admin(db, settings)
