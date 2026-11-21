"""The Alembic migration must produce the same schema as the models (CLAUDE.md §8)."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from app.config import Settings
from app.db import Base

BACKEND_DIR = Path(__file__).resolve().parent.parent


def test_upgrade_head_matches_the_models(settings: Settings) -> None:
    command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")

    engine = create_engine(f"sqlite:///{settings.db_path}")
    inspector = inspect(engine)
    for table in Base.metadata.sorted_tables:
        assert inspector.has_table(table.name), table.name
        migrated = {column["name"] for column in inspector.get_columns(table.name)}
        assert migrated == {column.name for column in table.columns}, table.name

    with engine.connect() as connection:
        sql = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE name = 'uq_users_single_admin'")
        ).scalar_one()
    assert "WHERE role = 'admin'" in sql
    engine.dispose()
