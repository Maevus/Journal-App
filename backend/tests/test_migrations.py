"""Alembic migration guards.

`test_migration_schema_matches_models` is the drift guard: it builds the schema
two ways — by running the migrations, and from SQLModel's metadata — and asserts
they declare the same tables and columns. It fails when a model gains/loses a
column (or table) without a matching migration.

`test_postgres_upgrade_downgrade_roundtrips` exercises the real Postgres path
(constraint enforcement, downgrade) but only when a Postgres URL is provided via
TEST_DATABASE_URL — skipped otherwise, so the SQLite unit suite stays green with
no external DB.
"""
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlmodel import SQLModel

from alembic import command
from alembic.config import Config
from conftest import require_test_database_url

_BACKEND = Path(__file__).resolve().parent.parent


def _alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)  # env.py honours an explicit url
    return cfg


def _schema_map(url: str) -> dict[str, list[str]]:
    """{table: [sorted column names]} for a DB, ignoring Alembic's bookkeeping."""
    engine = create_engine(url)
    try:
        insp = inspect(engine)
        return {
            table: sorted(col["name"] for col in insp.get_columns(table))
            for table in insp.get_table_names()
            if table != "alembic_version"
        }
    finally:
        engine.dispose()


def test_migration_schema_matches_models(tmp_path):
    from_migrations = f"sqlite:///{tmp_path / 'from_migrations.db'}"
    from_models = f"sqlite:///{tmp_path / 'from_models.db'}"

    command.upgrade(_alembic_config(from_migrations), "head")

    meta_engine = create_engine(from_models)
    SQLModel.metadata.create_all(meta_engine)
    meta_engine.dispose()

    assert _schema_map(from_migrations) == _schema_map(from_models), (
        "Migrations and models disagree on tables/columns — a model changed "
        "without a matching Alembic revision. Run `alembic revision --autogenerate`."
    )


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="set TEST_DATABASE_URL to a scratch Postgres DB (name must end in `_test`) "
    "to run the real-DB migration smoke test — it drops every table on the target",
)
def test_postgres_upgrade_downgrade_roundtrips():
    url = require_test_database_url()
    cfg = _alembic_config(url)

    command.upgrade(cfg, "head")
    tables = set(_schema_map(url))
    assert {"users", "journalentry", "sessions", "user_api_keys", "llmusage"} <= tables

    command.downgrade(cfg, "base")
    assert _schema_map(url) == {}  # every table dropped


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="set TEST_DATABASE_URL to a scratch Postgres DB (name must end in `_test`) "
    "to run the real-DB constraint test — it drops every table on the target",
)
def test_postgres_composite_unique_scopes_dedup_per_user():
    """The dedup unique is (user_id, content_hash): identical text from two users
    both persist, but the same user can't hold the same hash twice."""
    from sqlalchemy.exc import IntegrityError
    from sqlmodel import Session

    from models.journal import JournalEntry
    from models.user import User

    url = require_test_database_url()
    cfg = _alembic_config(url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    try:
        with Session(engine) as s:
            s.add_all([User(google_sub="a", email="a@x"), User(google_sub="b", email="b@x")])
            s.commit()
            s.add_all([
                JournalEntry(user_id=1, source="text", content="same", content_hash="h"),
                JournalEntry(user_id=2, source="text", content="same", content_hash="h"),
            ])
            s.commit()  # different owners, same hash → both allowed

        with Session(engine) as s:
            s.add(JournalEntry(user_id=1, source="text", content="same", content_hash="h"))
            with pytest.raises(IntegrityError):
                s.commit()  # same owner + hash → rejected
    finally:
        engine.dispose()
        command.downgrade(cfg, "base")
