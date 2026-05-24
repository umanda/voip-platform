"""
Alembic async migration environment.

IMPORTANT: We are connecting to the EXISTING galaxy_2 PostgreSQL database.
The schema was reverse-engineered in Phase 0 (docs/legacy-audit/schema-map.md).
We are NOT creating a new schema — do not run autogenerate blindly.

Safe operations:
  - alembic revision --autogenerate  (review before applying)
  - alembic upgrade head              (apply reviewed migrations only)

Dangerous operations to avoid:
  - Never DROP TABLE on any legacy table
  - Never RENAME COLUMN on any legacy table (will break PHP Sentinel in parallel)
  - Always test migrations on a staging copy of galaxy_2 first
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.models.db import Base  # imports all models — required for autogenerate

config = context.config
_settings = get_settings()

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (SQL script output)."""
    url = _settings.database_url
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations against the live galaxy_2 database (async driver)."""
    engine = create_async_engine(_settings.database_url)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
