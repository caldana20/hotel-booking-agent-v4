from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatRequest(StrictModel):
    session_id: UUID | None = None
    user_id: str = Field(min_length=1, max_length=256)
    message: str = Field(min_length=1, max_length=6000)


class ToolEvent(StrictModel):
    tool_name: str
    status: Literal["OK", "ERROR"]
    latency_ms: int
    retries: int = 0
    result_counts: dict[str, int] | None = None
    # Optional debug fields for the web UI debug panel.
    path: str | None = None
    url: str | None = None
    payload: dict | None = None
    response_keys: list[str] | None = None
    result_preview: dict | None = None


class OfferCard(StrictModel):
    offer_id: UUID
    hotel_id: UUID
    hotel_name: str
    city: str | None = None
    neighborhood: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    star_rating: float | None = None

    total_price: float
    taxes_total: float
    fees_total: float
    refundable: bool
    cancellation_deadline: datetime | None
    inventory_status: str
    last_priced_ts: datetime
    expires_ts: datetime
    room_type: str
    bed_config: str | None
    rate_plan: str


class GuardrailState(StrictModel):
    tool_calls: int
    wall_clock_ms: int


class ChatResponse(StrictModel):
    session_id: UUID
    trace_id: str
    agent_state: str
    assistant_message: str
    recommended_offers: list[OfferCard] = Field(default_factory=list)
    tool_timeline: list[ToolEvent] = Field(default_factory=list)
    guardrails: GuardrailState


class SessionListItem(StrictModel):
    session_id: UUID
    updated_at: datetime


class SessionListResponse(StrictModel):
    sessions: list[SessionListItem]


class SessionDetailResponse(StrictModel):
    session_id: UUID
    updated_at: datetime
    agent_state: str
    constraints: dict
    snapshot: dict


class ImportSessionRequest(StrictModel):
    session_id: UUID
    user_id: str
    agent_state: str
    constraints: dict
    snapshot: dict


class AdminSeedRequest(StrictModel):
    seed: int = 1337
    hotels: int = 220
    offers: int = 2600
    full_year_2026: bool = False
    baseline_adults: list[int] | None = None

