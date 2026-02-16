from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def postgres_url() -> str:
    with PostgresContainer("postgres:16") as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="session")
def migrated_seeded_db(postgres_url: str) -> str:
    # Normalize testcontainers URL (may be postgresql:// or postgresql+psycopg2://).
    base = postgres_url.replace("postgresql+psycopg2://", "postgresql://")
    # Alembic expects sync URL (psycopg3).
    sync_url = base.replace("postgresql://", "postgresql+psycopg://")
    async_url = base.replace("postgresql://", "postgresql+asyncpg://")

    os.environ["DATABASE_URL"] = async_url
    os.environ["DEFAULT_TENANT_ID"] = "t_default"

    alembic_ini = str(REPO_ROOT / "db" / "migrations" / "alembic.ini")
    cfg = Config(alembic_ini)
    os.environ["DATABASE_URL"] = sync_url
    command.upgrade(cfg, "head")

    # Seed
    from db.seed import seed

    seed(database_url=sync_url, tenant_id="t_default", seed_value=1337, hotels_n=220, offers_n=2600)

    # Restore async url for app runtime
    os.environ["DATABASE_URL"] = async_url
    return async_url

