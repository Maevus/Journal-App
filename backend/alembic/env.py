"""Alembic environment — wired to the app's own config and models.

The URL comes from `config.DATABASE_URL` (the single knob the app already uses),
not from alembic.ini, so `alembic` on the CLI and the programmatic upgrade in
main.py both target whatever DB the app is pointed at. `target_metadata` is
SQLModel's registry, populated by importing every model module below, so
`--autogenerate` sees the full schema.
"""
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Make the app package importable when Alembic runs from backend/ (CLI) or via
# a programmatic Config constructed in main.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import config as app_config  # noqa: E402

# Import every model so its table registers on SQLModel.metadata before
# autogenerate/compare runs. Importing the module is the whole point — the names
# are unused here.
import models.journal  # noqa: F401,E402
import models.keys  # noqa: F401,E402
import models.rate_limit  # noqa: F401,E402
import models.usage  # noqa: F401,E402
import models.user  # noqa: F401,E402
from sqlmodel import SQLModel  # noqa: E402

# Alembic Config (from the ini on the CLI; empty when constructed programmatically).
# Configure logging only when an ini file is actually present.
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata

# URL resolution: an explicitly-provided url (a programmatic Config, or the ini)
# wins; otherwise fall back to the app's own DB knob. This lets main.py's
# programmatic upgrade and the CLI both hit config.DATABASE_URL, while tests can
# point Alembic at a scratch DB by setting sqlalchemy.url on their Config.
_db_url = config.get_main_option("sqlalchemy.url") or app_config.DATABASE_URL
# Alembic stores this in a ConfigParser, where % is interpolation syntax — a
# URL-encoded password (postgres://u:p%40ss@…, which managed hosts generate
# routinely) would raise ValueError here. Escape on write; get_main_option
# un-escapes on read, so SQLAlchemy still receives the original URL.
config.set_main_option("sqlalchemy.url", _db_url.replace("%", "%%"))

# Batch mode lets future ALTERs work on SQLite (demo/tests), which can't ALTER
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
