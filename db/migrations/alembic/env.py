from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool


# Alembic Config object, provides access to values within the .ini file.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _get_database_url() -> str:
    # Prefer env var to support docker/local easily.
    url = os.getenv("DATABASE_URL")
    if url:
        # Alembic uses a sync driver. Normalize common runtime URLs.
        url = url.replace("postgresql+asyncpg://", "postgresql+psycopg://")
        # Some libraries (and some testcontainers versions) may emit psycopg2 URLs.
        url = url.replace("postgresql+psycopg2://", "postgresql+psycopg://")
        # If no driver is specified, SQLAlchemy defaults to psycopg2; force psycopg3.
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url
    # Fallback to ini
    ini_url = config.get_main_option("sqlalchemy.url")
    if ini_url:
        return ini_url
    return "postgresql+psycopg://app:app@localhost:5432/hotel"


def run_migrations_offline() -> None:
    url = _get_database_url()
    context.configure(
        url=url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _get_database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

