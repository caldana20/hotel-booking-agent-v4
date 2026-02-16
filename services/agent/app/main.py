from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession

from db.seed import seed as seed_db
from services.agent.app.db import ENGINE, get_session
from services.agent.app.graph import build_graph
from services.agent.app.logging import configure_logging, logger
from services.agent.app.model import ModelConfigError
from services.agent.app.observability import add_metrics_middleware, instrument_sqlalchemy, setup_tracing
from services.agent.app.persistence import hash_user_id, load_snapshot, new_session_id, upsert_snapshot
from services.agent.app.schemas import (
    AdminSeedRequest,
    ChatRequest,
    ChatResponse,
    GuardrailState,
    ImportSessionRequest,
    SessionDetailResponse,
    SessionListResponse,
)
from services.agent.app.settings import SETTINGS


app = FastAPI(title="Hotel Shopping Agent API", version="0.1.0")
configure_logging(SETTINGS.log_level)
setup_tracing(app, service_name="agent")
add_metrics_middleware(app, service_name="agent")
instrument_sqlalchemy(ENGINE)

GRAPH = build_graph()

# Local dev UI runs on :3000
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _reset_per_turn_state(state: dict) -> None:
    # Per-turn state: ensure tool timeline is not cumulative across turns.
    # We keep prior snapshot data (e.g. recommended_offers) for fallback, but tool timeline should reflect the current turn.
    state["tool_timeline"] = []
    state["tool_calls_this_turn"] = 0
    state.pop("llm_action", None)
    # Clear per-turn selection control flow so we don't get stuck on later turns.
    state.pop("selected_offer_id", None)
    state.pop("_selection_this_turn", None)


def _trace_id_hex() -> str:
    span = trace.get_current_span()
    ctx = span.get_span_context()
    return f"{ctx.trace_id:032x}"


@app.get("/healthz")
async def healthz(session: AsyncSession = Depends(get_session)) -> dict:
    await session.execute(sa.text("SELECT 1"))
    return {"ok": True}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, session: AsyncSession = Depends(get_session)) -> ChatResponse:
    start = time.perf_counter()

    session_id = req.session_id or new_session_id()
    user_hash = hash_user_id(req.user_id)

    # Load state from snapshot if it exists.
    snap = await load_snapshot(session, session_id)
    state = {}
    if snap:
        state = snap.get("snapshot") or {}
        state["constraints"] = snap.get("constraints") or {}

    _reset_per_turn_state(state)

    # Set current turn input.
    state["session_id"] = str(session_id)
    state["user_message"] = req.message

    # Execute graph (single pass per /chat).
    try:
        out = await GRAPH.ainvoke(state)
    except ModelConfigError as e:
        # No deterministic fallback at runtime: fail clearly so it’s obvious the LLM is required.
        raise HTTPException(status_code=500, detail=str(e)) from e

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    if elapsed_ms > SETTINGS.max_wall_clock_ms:
        logger.info("fallback_triggered", kind="max_wall_clock_ms")

    assistant_message = out.get("assistant_message") or ""
    agent_state = out.get("agent_state") or "UNKNOWN"
    tool_timeline = out.get("tool_timeline") or []
    recommended_offers = out.get("recommended_offers") or []

    trace_id = _trace_id_hex()
    # Update snapshot metadata for UI.
    recent_traces = (out.get("recent_trace_ids") or [])[-9:]
    recent_traces.append(trace_id)

    # Per-turn record for UI/replay/debug.
    turns = list((state.get("turns") or []))
    turn_record = {
        "ts": datetime.now(tz=UTC).isoformat(),
        "trace_id": trace_id,
        "user_message": req.message,
        "agent_state": agent_state,
        "assistant_message": assistant_message,
        "tool_timeline": tool_timeline,
        "recommended_offers": recommended_offers,
        "selected_offer_id": out.get("selected_offer_id"),
        "last_selected_offer_id": out.get("last_selected_offer_id"),
    }
    turns.append(turn_record)
    # Bound history to keep snapshots small.
    turns = turns[-50:]

    snapshot_payload = {
        # Conversation-level state
        "agent_state": agent_state,
        "assistant_message": assistant_message,
        "tool_timeline": tool_timeline,
        "turns": turns,
        "candidates": out.get("candidates") or [],
        "offers": out.get("offers") or [],
        "ranked_offers": out.get("ranked_offers") or [],
        "reasons": out.get("reasons") or [],
        "recommended_offers": recommended_offers,
        # Fingerprint of constraints used to produce cached tool state.
        "tool_constraints_key": out.get("tool_constraints_key"),
        "selected_offer_id": out.get("selected_offer_id"),
        "last_selected_offer_id": out.get("last_selected_offer_id"),
        "recent_trace_ids": recent_traces,
        "updated_at": datetime.now(tz=UTC).isoformat(),
    }

    constraints = out.get("constraints") or {}
    await upsert_snapshot(
        session,
        session_id=session_id,
        user_id_hash=user_hash,
        agent_state=agent_state,
        constraints=constraints,
        snapshot=snapshot_payload,
    )

    logger.info(
        "response_sent",
        session_id=str(session_id),
        trace_id=trace_id,
        agent_state=agent_state,
        elapsed_ms=elapsed_ms,
    )

    return ChatResponse(
        session_id=session_id,
        trace_id=trace_id,
        agent_state=agent_state,
        assistant_message=assistant_message,
        recommended_offers=recommended_offers,
        tool_timeline=tool_timeline,
        guardrails=GuardrailState(tool_calls=len(tool_timeline), wall_clock_ms=elapsed_ms),
    )


@app.get("/sessions", response_model=SessionListResponse)
async def list_sessions(session: AsyncSession = Depends(get_session)) -> SessionListResponse:
    q = sa.text(
        "SELECT session_id, updated_at FROM session_snapshots WHERE tenant_id=:t ORDER BY updated_at DESC LIMIT 200"
    )
    rows = (await session.execute(q, {"t": SETTINGS.default_tenant_id})).mappings().all()
    return SessionListResponse(sessions=[{"session_id": r["session_id"], "updated_at": r["updated_at"]} for r in rows])


@app.get("/sessions/{session_id}", response_model=SessionDetailResponse)
async def get_session_detail(session_id: UUID, session: AsyncSession = Depends(get_session)) -> SessionDetailResponse:
    snap = await load_snapshot(session, session_id)
    if not snap:
        raise HTTPException(status_code=404, detail="session not found")
    return SessionDetailResponse(
        session_id=snap["session_id"],
        updated_at=snap["updated_at"],
        agent_state=snap["agent_state"],
        constraints=snap["constraints"],
        snapshot=snap["snapshot"],
    )


def _require_admin(x_admin_token: str | None) -> None:
    if not x_admin_token or x_admin_token != SETTINGS.admin_token:
        raise HTTPException(status_code=403, detail="forbidden")


@app.post("/sessions/import")
async def import_session(
    req: ImportSessionRequest,
    x_admin_token: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    _require_admin(x_admin_token)
    user_hash = hash_user_id(req.user_id)
    await upsert_snapshot(
        session,
        session_id=req.session_id,
        user_id_hash=user_hash,
        agent_state=req.agent_state,
        constraints=req.constraints,
        snapshot=req.snapshot,
    )
    return {"ok": True}


@app.post("/admin/seed")
async def admin_seed(
    req: AdminSeedRequest,
    x_admin_token: str | None = Header(default=None),
) -> dict:
    _require_admin(x_admin_token)
    # Use sync seed helper; run inside request (dev only).
    seed_db(
        database_url=SETTINGS.database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://"),
        tenant_id=SETTINGS.default_tenant_id,
        seed_value=req.seed,
        hotels_n=req.hotels,
        offers_n=req.offers,
        full_year_2026=req.full_year_2026,
        baseline_adults=req.baseline_adults,
    )
    return {"ok": True}


@app.post("/admin/clear_sessions")
async def admin_clear_sessions(
    x_admin_token: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    _require_admin(x_admin_token)
    await session.execute(sa.text("TRUNCATE TABLE session_snapshots"))
    await session.commit()
    return {"ok": True}

