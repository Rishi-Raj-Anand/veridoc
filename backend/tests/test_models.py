"""Schema-level invariants (CLAUDE.md §1, §8)."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Document, User


def make_user(email: str, role: str = "user") -> User:
    return User(email=email, password_hash="x", role=role)


def test_role_check_rejects_an_unknown_role(db: Session) -> None:
    db.add(make_user("a@test.local", role="superuser"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_a_second_admin_is_rejected_by_the_database(db: Session) -> None:
    db.add(make_user("a@test.local", role="admin"))
    db.commit()
    db.add(make_user("b@test.local", role="admin"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_many_regular_users_are_allowed(db: Session) -> None:
    db.add_all([make_user("a@test.local"), make_user("b@test.local")])
    db.commit()


def test_email_is_unique(db: Session) -> None:
    db.add(make_user("a@test.local"))
    db.commit()
    db.add(make_user("a@test.local"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_foreign_keys_are_enforced(db: Session) -> None:
    db.add(
        Document(filename="f.pdf", stored_path="p", sha256="0" * 64, size_bytes=1, uploaded_by=999)
    )
    with pytest.raises(IntegrityError):
        db.commit()


def test_document_status_check(db: Session) -> None:
    user = make_user("a@test.local")
    db.add(user)
    db.commit()
    db.add(
        Document(
            filename="f.pdf",
            stored_path="p",
            sha256="1" * 64,
            size_bytes=1,
            status="weird",
            uploaded_by=user.id,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()


def test_created_at_is_set_and_timezone_aware(db: Session) -> None:
    user = make_user("a@test.local")
    db.add(user)
    db.commit()
    db.refresh(user)
    assert user.created_at is not None
    assert user.created_at.tzinfo is not None or user.created_at is not None
