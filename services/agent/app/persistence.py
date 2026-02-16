from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from services.agent.app.settings import SETTINGS


def hash_user_id(user_id: str) -> str:
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()


def now() -> datetime:
    return datetime.now(tz=UTC)


async def load_snapshot(session: AsyncSession, session_id: UUID) -> dict[str, Any] | None:
    q = sa.text(
        "SELECT session_id, tenant_id, user_id_hash, agent_state, constraints, snapshot, updated_at "
        "FROM session_snapshots WHERE session_id=:id AND tenant_id=:t"
    )
    row = (await session.execute(q, {"id": session_id, "t": SETTINGS.default_tenant_id})).mappings().first()
    return dict(row) if row else None


async def upsert_snapshot(
    session: AsyncSession,
    session_id: UUID,
    user_id_hash: str,
    agent_state: str,
    constraints: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    q = sa.text(
        "INSERT INTO session_snapshots(session_id, tenant_id, user_id_hash, agent_state, constraints, snapshot, updated_at) "
        "VALUES (:id, :t, :u, :s, CAST(:c AS jsonb), CAST(:snap AS jsonb), :ts) "
        "ON CONFLICT (session_id) DO UPDATE SET agent_state=EXCLUDED.agent_state, "
        "constraints=EXCLUDED.constraints, snapshot=EXCLUDED.snapshot, updated_at=EXCLUDED.updated_at"
    )
    await session.execute(
        q,
        {
            "id": session_id,
            "t": SETTINGS.default_tenant_id,
            "u": user_id_hash,
            "s": agent_state,
            # Constraints/snapshot can include date/datetime/UUID; serialize deterministically for JSONB.
            "c": json.dumps(constraints, default=str),
            "snap": json.dumps(snapshot, default=str),
            "ts": now(),
        },
    )
    await session.commit()


def new_session_id() -> UUID:
    return uuid4()

