"""SQLAlchemy engine, session factory and declarative base (CLAUDE.md §4, §8).

The backend uses the synchronous SQLAlchemy API: SQLite has no async driver in this
project, so database-bound routes are declared ``def`` and FastAPI runs them in its own
threadpool (§14).
"""

from __future__ import annotations

import sqlite3
from functools import lru_cache

from sqlalchemy import Engine, MetaData, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

# Stable constraint names: SQLite needs them for Alembic batch ALTER, and they keep
# autogenerate diffs quiet.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _enable_foreign_keys(dbapi_connection: sqlite3.Connection, _record: object) -> None:
    """SQLite ignores foreign keys unless every connection turns enforcement on."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@lru_cache
def get_engine() -> Engine:
    """The process-wide engine. Lazy: nothing touches the disk until first use."""
    settings = get_settings()
    engine = create_engine(
        f"sqlite:///{settings.db_path}",
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _enable_foreign_keys)
    return engine


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(get_engine(), expire_on_commit=False)


def reset_engine() -> None:
    """Dispose the engine and forget it, so tests can repoint ``DATA_DIR``."""
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_sessionmaker.cache_clear()
    get_engine.cache_clear()
