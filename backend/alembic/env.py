"""Alembic environment — wired to the app's own config and models.

URL comes from `config.DATABASE_URL`, not alembic.ini, so the CLI and
main.py's programmatic upgrade both target the same DB. `target_metadata`
is SQLModel's registry, populated by importing every model module below.
"""
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Makes the app package importable whether Alembic runs via CLI or main.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import config as app_config  # noqa: E402

# Registers each model's table on SQLModel.metadata before autogenerate
# runs; importing the module is the whole point, the names are unused.
import models.journal  # noqa: F401,E402
import models.keys  # noqa: F401,E402
import models.ocr  # noqa: F401,E402
import models.rate_limit  # noqa: F401,E402
import models.usage  # noqa: F401,E402
import models.user  # noqa: F401,E402
from sqlmodel import SQLModel  # noqa: E402

# Config is from the ini on the CLI, empty when constructed programmatically.
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata

# Explicit url (programmatic Config or ini) wins; otherwise falls back to
# the app's DB knob — lets tests point Alembic at a scratch DB via
# sqlalchemy.url on their Config.
_db_url = config.get_main_option("sqlalchemy.url") or app_config.DATABASE_URL
# ConfigParser treats % as interpolation syntax — a URL-encoded password
# (%40 for @, common on managed hosts) would raise ValueError here. Escape
# on write; get_main_option un-escapes on read, so SQLAlchemy gets the
# original URL.
config.set_main_option("sqlalchemy.url", _db_url.replace("%", "%%"))

# Batch mode lets future ALTERs work on SQLite, which can't ALTER
# constraints in place; harmless on Postgres.
_render_as_batch = _db_url.startswith("sqlite")


def run_migrations_offline() -> None:
    context.configure(
        url=_db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=_render_as_batch,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=_render_as_batch,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
