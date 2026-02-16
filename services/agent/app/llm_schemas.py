from __future__ import annotations

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LLMConstraints(StrictModel):
    city: str | None = None
    check_in: date | None = None
    check_out: date | None = None
    adults: int | None = Field(default=None, ge=1, le=10)
    children: int | None = Field(default=None, ge=0, le=10)
    rooms: int | None = Field(default=None, ge=1, le=5)
    max_price: float | None = Field(default=None, gt=0)
    min_star: float | None = Field(default=None, ge=1, le=5)
    amenities: list[str] | None = None
    currency: str | None = None
    # Preferences that do not map 1:1 to tool payload (used for decision/ranking messaging).
    refundable_preferred: bool | None = None


class LLMExtraction(StrictModel):
    """
    LLM-only extraction result. No defaults should be assumed by code.
    """

    constraints_update: LLMConstraints | None = None
    offer_id: UUID | None = None


class LLMDateResolve(StrictModel):
    """
    Structured result for resolving concrete dates.

    The model must either:
    - provide both check_in and check_out, OR
    - set needs_clarification=true and provide a single question.
    """

    check_in: date | None = None
    check_out: date | None = None
    needs_clarification: bool = False
    question: str | None = None


class LLMCityResolve(StrictModel):
    city: str | None = None
    needs_clarification: bool = False
    question: str | None = None


class LLMOccupancyResolve(StrictModel):
    adults: int | None = Field(default=None, ge=1, le=10)
    children: int | None = Field(default=None, ge=0, le=10)
    rooms: int | None = Field(default=None, ge=1, le=5)
    needs_clarification: bool = False
    question: str | None = None


class LLMAmenitiesResolve(StrictModel):
    amenities: list[str] | None = None
    refundable_preferred: bool | None = None
    needs_clarification: bool = False
    question: str | None = None


class LLMBudgetResolve(StrictModel):
    max_price: float | None = Field(default=None, gt=0)


HardFilterKey = Literal["max_price", "min_star", "amenities", "refundable_preferred"]


class LLMHardFiltersSet(StrictModel):
    max_price: float | None = Field(default=None, gt=0)
    min_star: float | None = Field(default=None, ge=1, le=5)
    amenities: list[str] | None = None
    refundable_preferred: bool | None = None


class LLMHardFiltersPatch(StrictModel):
    # Set/replace these filter values (omit when no update).
    set: LLMHardFiltersSet | None = None
    # Explicitly clear previously set hard filters.
    clear: list[HardFilterKey] = Field(default_factory=list)

ToolName = Literal["search_candidates", "get_offers", "rank_offers"]


class AgentActionCallTool(StrictModel):
    type: Literal["call_tool"]
    tool_name: ToolName
    # Payload is included for transparency/debugging, but the server may override/normalize it.
    payload: dict = Field(default_factory=dict)
    # Reserved for future tool calls that require an explicit offer_id.
    # Not used in the current (no-repricing) workflow.
    offer_id: UUID | None = None
    constraints_update: LLMConstraints | None = None


class AgentActionRespond(StrictModel):
    type: Literal["respond"]
    # A short intent for the response; server uses it to choose the prompt template.
    kind: Literal["clarify", "explain", "confirm", "generic"] = "generic"
    message: str | None = None
    constraints_update: LLMConstraints | None = None
    recommended_offer_ids: list[UUID] | None = None


AgentAction = Annotated[AgentActionCallTool | AgentActionRespond, Field(discriminator="type")]

# Helper for validating discriminated unions (AgentAction is a typing object, not a BaseModel).
AGENT_ACTION_ADAPTER: TypeAdapter[AgentAction] = TypeAdapter(AgentAction)

