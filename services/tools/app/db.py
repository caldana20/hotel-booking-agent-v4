from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from services.tools.app.settings import SETTINGS


def create_engine() -> AsyncEngine:
    # NullPool avoids cross-event-loop pooled connections during tests and keeps behavior simple.
    return create_async_engine(SETTINGS.database_url, pool_pre_ping=True, poolclass=NullPool)


ENGINE = create_engine()
SESSIONMAKER = async_sessionmaker(ENGINE, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SESSIONMAKER() as session:
        yield session


def enforce_tenant(tenant_id: str) -> str:
    if tenant_id != SETTINGS.default_tenant_id:
        # Single-tenant MVP: reject non-default tenant ids.
        raise ValueError("invalid tenant_id")
    return tenant_id

