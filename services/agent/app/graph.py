from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

from services.agent.app.constraints import Constraints
from services.agent.app.graph_helpers import (
    clarify_message as _clarify_message,
    extract_first_json_object as _extract_first_json_object,
    find_selected_offer as _find_selected_offer,
    merge_constraints_dict as _merge_constraints_dict,
    missing_required_fields as _missing_required_fields,
    parse_dt as _parse_dt,
    tool_constraint_subset as _tool_constraint_subset,
)
from services.agent.app.logging import logger
from services.agent.app.grounding import GroundingViolation, validate_grounded_response
from services.agent.app.llm_schemas import (
    AGENT_ACTION_ADAPTER,
    AgentActionCallTool,
    AgentActionRespond,
    LLMConstraints,
    LLMAmenitiesResolve,
    LLMHardFiltersPatch,
    LLMBudgetResolve,
    LLMCityResolve,
    LLMDateResolve,
    LLMExtraction,
    LLMOccupancyResolve,
)
from services.agent.app.model import get_chat_model
from services.agent.app.observability import FALLBACK_TOTAL
from services.agent.app.prompt import (
    AMENITIES_RESOLVE_SYSTEM_PROMPT,
    AMENITIES_RESOLVE_TEMPLATE,
    BUDGET_RESOLVE_SYSTEM_PROMPT,
    BUDGET_RESOLVE_TEMPLATE,
    CITY_RESOLVE_SYSTEM_PROMPT,
    CITY_RESOLVE_TEMPLATE,
    DECIDE_SYSTEM_PROMPT,
    DECIDE_TEMPLATE,
    DATE_RESOLVE_SYSTEM_PROMPT,
    DATE_RESOLVE_TEMPLATE,
    EXTRACT_TEMPLATE,
    EXTRACT_SYSTEM_PROMPT,
    HARD_FILTERS_RESOLVE_SYSTEM_PROMPT,
    HARD_FILTERS_RESOLVE_TEMPLATE,
    OCCUPANCY_RESOLVE_SYSTEM_PROMPT,
    OCCUPANCY_RESOLVE_TEMPLATE,
    RESPOND_TEMPLATE,
    SYSTEM_PROMPT,
)
from services.agent.app.settings import SETTINGS
from services.agent.app.tool_client import ToolClient


class GraphState(TypedDict, total=False):
    # Request/session
    session_id: str
    user_message: str
    # Constraints
    constraints: dict[str, Any]
    clarifying_asked: bool
    # Tool data
    candidates: list[dict[str, Any]]
    offers: list[dict[str, Any]]
    ranked_offers: list[dict[str, Any]]
    reasons: list[dict[str, Any]]
    tool_timeline: list[dict[str, Any]]
    recommended_offers: list[dict[str, Any]]
    recent_trace_ids: list[str]
    # Selection
    selected_offer_id: str | None
    last_selected_offer_id: str | None
    # Fingerprint tying cached tool state to constraints (used for invalidation).
    tool_constraints_key: str | None
    # Outputs
    assistant_message: str
    agent_state: str
    # LLM decision loop
    llm_action: dict[str, Any]
    tool_calls_this_turn: int
    _end_turn: bool


_TOOL_AMENITY_ALLOWLIST = {
    "wifi",
    "breakfast_included",
    "pool",
    "gym",
    "parking",
    "pet_friendly",
    "airport_shuttle",
    "spa",
    "restaurant",
    "bar",
}


async def _resolve_non_date_fields(state: GraphState, *, llm: Any, user_msg: str, today_utc: Any) -> GraphState:
    """
    Resolver layer: like DATE_RESOLVE, but for other fields.

    Python does not parse user text; it only decides which resolvers to run based on the structured state
    and validates resolver JSON with strict schemas.
    """
    resolver_state_json = {
        "today_utc": str(today_utc),
        "constraints": state.get("constraints") or {},
        "recent_turns": (state.get("turns") or [])[-6:],
        "tool_supported_amenities": sorted(_TOOL_AMENITY_ALLOWLIST),
    }

    # City: if missing.
    cur = state.get("constraints") or {}
    if not cur.get("city"):
        prompt = CITY_RESOLVE_TEMPLATE.format(user_message=user_msg, state_json=json.dumps(resolver_state_json, default=str))
        sys = SystemMessage(content=CITY_RESOLVE_SYSTEM_PROMPT)
        human = HumanMessage(content=prompt)
        for attempt in range(3):
            try:
                raw = (await llm.ainvoke([sys, human])).content  # type: ignore[attr-defined]
                obj = json.loads(_extract_first_json_object(str(raw)))
                res = LLMCityResolve.model_validate(obj)
                if res.needs_clarification:
                    # Keep city questions deterministic (avoid "Which Austin...").
                    q = _clarify_message(["city"], cur)
                    state["assistant_message"] = q
                    state["agent_state"] = "COLLECT_CONSTRAINTS"
                    state["_end_turn"] = True
                    return state
                if res.city:
                    state["constraints"] = _merge_constraints_dict(state.get("constraints") or {}, LLMConstraints(city=res.city))
                break
            except Exception as e:  # noqa: BLE001
                logger.info("llm_city_resolve_failed", attempt=attempt, error=str(e))
                human = HumanMessage(content=prompt + "\n\nReturn ONLY a single JSON object.")

    # Occupancy: if missing adults or rooms.
    cur = state.get("constraints") or {}
    if not cur.get("adults") or not cur.get("rooms"):
        prompt = OCCUPANCY_RESOLVE_TEMPLATE.format(
            user_message=user_msg, state_json=json.dumps(resolver_state_json, default=str)
        )
        sys = SystemMessage(content=OCCUPANCY_RESOLVE_SYSTEM_PROMPT)
        human = HumanMessage(content=prompt)
        for attempt in range(3):
            try:
                raw = (await llm.ainvoke([sys, human])).content  # type: ignore[attr-defined]
                obj = json.loads(_extract_first_json_object(str(raw)))
                res = LLMOccupancyResolve.model_validate(obj)
                if res.needs_clarification:
                    # Deterministic clarification so we always mention "rooms" when needed.
                    now = dict(state.get("constraints") or {})
                    missing: list[str] = []
                    if not now.get("adults"):
                        missing.append("adults")
                    if not now.get("rooms"):
                        missing.append("rooms")
                    q = _clarify_message(missing, now)
                    state["assistant_message"] = q
                    state["agent_state"] = "COLLECT_CONSTRAINTS"
                    state["_end_turn"] = True
                    return state
                upd = LLMConstraints(adults=res.adults, children=res.children, rooms=res.rooms)
                state["constraints"] = _merge_constraints_dict(state.get("constraints") or {}, upd)
                break
            except Exception as e:  # noqa: BLE001
                logger.info("llm_occupancy_resolve_failed", attempt=attempt, error=str(e))
                human = HumanMessage(content=prompt + "\n\nReturn ONLY a single JSON object.")

    # Amenities/refundable:
    # - If amenities are present but invalid, normalize them.
    # - If refundable_preferred is missing, give the model a chance to set it from the user message
    #   without any deterministic parsing in Python.
    cur = state.get("constraints") or {}
    amenities = cur.get("amenities") or []
    needs_amenity_fix = any(str(a) not in _TOOL_AMENITY_ALLOWLIST for a in amenities) if amenities else False
    # Only run this extra resolver on refinement turns (when we already have tool state),
    # and only at the start of the user turn (before any tool calls).
    # This prevents the resolver from interrupting the in-turn tool pipeline.
    has_tool_state = bool(
        state.get("recommended_offers") or state.get("ranked_offers") or state.get("offers") or state.get("candidates")
    )
    at_turn_start = int(state.get("tool_calls_this_turn") or 0) == 0
    needs_refundable_resolve = (cur.get("refundable_preferred") is None) and has_tool_state and at_turn_start
    if needs_amenity_fix or needs_refundable_resolve:
        prompt = AMENITIES_RESOLVE_TEMPLATE.format(
            user_message=user_msg, state_json=json.dumps(resolver_state_json, default=str)
        )
        sys = SystemMessage(content=AMENITIES_RESOLVE_SYSTEM_PROMPT)
        human = HumanMessage(content=prompt)
        for attempt in range(3):
            try:
                raw = (await llm.ainvoke([sys, human])).content  # type: ignore[attr-defined]
                obj = json.loads(_extract_first_json_object(str(raw)))
                res = LLMAmenitiesResolve.model_validate(obj)
                if res.needs_clarification:
                    # Amenities are optional; never block the tool pipeline on them.
                    # If the model asks a question here, ignore it and proceed.
                    logger.info("llm_amenities_resolve_skipped_question", question=res.question)
                    break
                upd = LLMConstraints(amenities=res.amenities, refundable_preferred=res.refundable_preferred)
                state["constraints"] = _merge_constraints_dict(state.get("constraints") or {}, upd)
                break
            except Exception as e:  # noqa: BLE001
                logger.info("llm_amenities_resolve_failed", attempt=attempt, error=str(e))
                human = HumanMessage(content=prompt + "\n\nReturn ONLY a single JSON object.")

    return state


async def _resolve_budget(state: GraphState, *, llm: Any, user_msg: str, today_utc: Any) -> GraphState:
    cur = state.get("constraints") or {}
    if cur.get("max_price"):
        return state
    resolver_state_json = {
        "today_utc": str(today_utc),
        "constraints": state.get("constraints") or {},
        "recent_turns": (state.get("turns") or [])[-6:],
    }
    prompt = BUDGET_RESOLVE_TEMPLATE.format(user_message=user_msg, state_json=json.dumps(resolver_state_json, default=str))
    sys = SystemMessage(content=BUDGET_RESOLVE_SYSTEM_PROMPT)
    human = HumanMessage(content=prompt)
    for attempt in range(3):
        try:
            raw = (await llm.ainvoke([sys, human])).content  # type: ignore[attr-defined]
            obj = json.loads(_extract_first_json_object(str(raw)))
            res = LLMBudgetResolve.model_validate(obj)
            if res.max_price:
                state["constraints"] = _merge_constraints_dict(
                    state.get("constraints") or {}, LLMConstraints(max_price=float(res.max_price))
                )
            break
        except Exception as e:  # noqa: BLE001
            logger.info("llm_budget_resolve_failed", attempt=attempt, error=str(e))
            human = HumanMessage(content=prompt + "\n\nReturn ONLY a single JSON object.")
    return state


async def _resolve_hard_filters_patch(state: GraphState, *, llm: Any, user_msg: str, today_utc: Any) -> GraphState:
    """
    Optional hard filters are updatable at any time (set OR clear).

    This resolver is non-blocking (no questions) and only runs at the start of a user turn
    so it cannot interrupt the in-turn tool pipeline.
    """
    if int(state.get("tool_calls_this_turn") or 0) != 0:
        return state

    resolver_state_json = {
        "today_utc": str(today_utc),
        "constraints": state.get("constraints") or {},
        "recent_turns": (state.get("turns") or [])[-6:],
        "tool_supported_amenities": sorted(_TOOL_AMENITY_ALLOWLIST),
    }
    prompt = HARD_FILTERS_RESOLVE_TEMPLATE.format(
        user_message=user_msg, state_json=json.dumps(resolver_state_json, default=str)
    )
    sys = SystemMessage(content=HARD_FILTERS_RESOLVE_SYSTEM_PROMPT)
    human = HumanMessage(content=prompt)
    for attempt in range(3):
        try:
            raw = (await llm.ainvoke([sys, human])).content  # type: ignore[attr-defined]
            obj = json.loads(_extract_first_json_object(str(raw)))
            patch = LLMHardFiltersPatch.model_validate(obj)

            if patch.clear:
                cur = dict(state.get("constraints") or {})
                for k in patch.clear:
                    cur.pop(k, None)
                state["constraints"] = cur

            if patch.set:
                upd = LLMConstraints(**patch.set.model_dump(exclude_none=True))
                state["constraints"] = _merge_constraints_dict(state.get("constraints") or {}, upd)
            break
        except Exception as e:  # noqa: BLE001
            logger.info("llm_hard_filters_resolve_failed", attempt=attempt, error=str(e))
            human = HumanMessage(content=prompt + "\n\nReturn ONLY a single JSON object.")

    return state


def build_graph() -> Any:
    sg: StateGraph = StateGraph(GraphState)
    sg.add_node("LLM_DECIDE", _llm_decide)
    sg.add_node("CALL_TOOL", _call_tool)
    sg.add_node("LLM_RESPOND", _llm_respond)

    sg.set_entry_point("LLM_DECIDE")

    sg.add_conditional_edges(
        "LLM_DECIDE",
        _route_after_llm_decide,
        {
            "CALL_TOOL": "CALL_TOOL",
            "RESPOND": "LLM_RESPOND",
            "END": END,
        },
    )
    sg.add_conditional_edges(
        "CALL_TOOL",
        _route_after_call_tool,
        {
            "DECIDE": "LLM_DECIDE",
            "RESPOND": "LLM_RESPOND",
        },
    )
    sg.add_edge("LLM_RESPOND", END)

    return sg.compile()


def _tool_constraints_key(constraints: dict[str, Any]) -> str:
    """
    Stable fingerprint of the constraints that affect tool results.

    We use this to invalidate cached candidates/offers when constraints change mid-session
    (e.g. user adds min_star after we already shopped once).
    """
    subset = _tool_constraint_subset(constraints or {})
    if isinstance(subset.get("check_in"), date):
        subset["check_in"] = subset["check_in"].isoformat()
    if isinstance(subset.get("check_out"), date):
        subset["check_out"] = subset["check_out"].isoformat()
    if isinstance(subset.get("amenities"), list):
        subset["amenities"] = sorted([str(a) for a in subset["amenities"]])
    return json.dumps(subset, sort_keys=True, default=str)


async def _llm_decide(state: GraphState) -> GraphState:
    state["agent_state"] = "LLM_DECIDE"
    state.setdefault("tool_calls_this_turn", 0)
    state.pop("_end_turn", None)

    user_msg = state.get("user_message", "")
    today_utc = datetime.now(tz=UTC).date()

    llm = get_chat_model()

    state_json = {
        "today_utc": today_utc.isoformat(),
        "constraints": state.get("constraints") or {},
        # Provide recent context so the model can carry forward previously provided slots
        # even if constraints_update was missed in a prior step.
        "recent_turns": (state.get("turns") or [])[-6:],
        "has_candidates": bool(state.get("candidates")),
        "candidates_n": len(state.get("candidates") or []),
        "has_offers": bool(state.get("offers")),
        "offers_n": len(state.get("offers") or []),
        "has_ranked_offers": bool(state.get("ranked_offers")),
        "ranked_offers_n": len(state.get("ranked_offers") or []),
        "tool_calls_this_turn": int(state.get("tool_calls_this_turn") or 0),
        "max_tool_calls_per_turn": SETTINGS.max_tool_calls_per_turn,
    }

    extracted_offer_id: str | None = None
    # Step 1: extraction/resolution runs only at the start of a user turn.
    # When the graph loops (tool -> decide -> tool) within the same /chat request,
    # re-extracting can drift constraints and cause tool invalidation loops.
    if int(state.get("tool_calls_this_turn") or 0) == 0:
        extract_prompt = EXTRACT_TEMPLATE.format(user_message=user_msg, state_json=json.dumps(state_json, default=str))
        extract_sys = SystemMessage(content=EXTRACT_SYSTEM_PROMPT)
        extract_human = HumanMessage(content=extract_prompt)
        for attempt in range(3):
            try:
                raw = (await llm.ainvoke([extract_sys, extract_human])).content  # type: ignore[attr-defined]
                obj = json.loads(_extract_first_json_object(str(raw)))
                # Some models emit offer_id:"" when no offer is selected; normalize for strict UUID parsing.
                if obj.get("offer_id") == "":
                    obj["offer_id"] = None
                ext = LLMExtraction.model_validate(obj)
                if ext.constraints_update:
                    state["constraints"] = _merge_constraints_dict(state.get("constraints") or {}, ext.constraints_update)
                if ext.offer_id:
                    extracted_offer_id = str(ext.offer_id)
                logger.info(
                    "llm_extract_ok",
                    constraints_update=ext.constraints_update.model_dump(exclude_none=True)
                    if ext.constraints_update
                    else None,
                    offer_id=extracted_offer_id,
                )
                break
            except Exception as e:  # noqa: BLE001
                logger.info("llm_extract_failed", attempt=attempt, error=str(e))
                extract_human = HumanMessage(
                    content=(
                        extract_prompt
                        + "\n\nReturn ONLY a single JSON object. No markdown, no prose."
                        + "\nIf the user provided a timeframe AND stay length (e.g. 'next week from now 3 day stay'), "
                        + "you MUST output concrete ISO check_in/check_out in constraints_update."
                    )
                )

    # Step 1b: date resolution contract.
    # If dates are missing after EXTRACT, ask the model to resolve them in a dedicated mode.
    parsed_after_extract = LLMConstraints.model_validate(state.get("constraints") or {}).model_dump(exclude_none=True)
    missing_after_extract = _missing_required_fields(parsed_after_extract)
    if "dates" in missing_after_extract and int(state.get("tool_calls_this_turn") or 0) == 0:
        date_state_json = {
            "today_utc": today_utc.isoformat(),
            "constraints": state.get("constraints") or {},
            "recent_turns": (state.get("turns") or [])[-6:],
        }
        date_prompt = DATE_RESOLVE_TEMPLATE.format(user_message=user_msg, state_json=json.dumps(date_state_json, default=str))
        date_sys = SystemMessage(content=DATE_RESOLVE_SYSTEM_PROMPT)
        date_human = HumanMessage(content=date_prompt)
        for attempt in range(3):
            try:
                raw = (await llm.ainvoke([date_sys, date_human])).content  # type: ignore[attr-defined]
                obj = json.loads(_extract_first_json_object(str(raw)))
                dr = LLMDateResolve.model_validate(obj)
                if dr.needs_clarification:
                    q = (dr.question or "").strip() or "What dates should I use? (YYYY-MM-DD to YYYY-MM-DD)"
                    state["assistant_message"] = q
                    state["agent_state"] = "COLLECT_CONSTRAINTS"
                    state["_end_turn"] = True
                    return state
                if dr.check_in and dr.check_out:
                    upd = LLMConstraints(check_in=dr.check_in, check_out=dr.check_out)
                    state["constraints"] = _merge_constraints_dict(state.get("constraints") or {}, upd)
                break
            except Exception as e:  # noqa: BLE001
                logger.info("llm_date_resolve_failed", attempt=attempt, error=str(e))
                date_human = HumanMessage(content=date_prompt + "\n\nReturn ONLY a single JSON object.")

    # Step 1c: resolve other fields (city/occupancy/amenities) via dedicated contracts.
    if int(state.get("tool_calls_this_turn") or 0) == 0:
        state = await _resolve_non_date_fields(state, llm=llm, user_msg=user_msg, today_utc=today_utc)
        if state.get("_end_turn"):
            return state

    # Step 1d: budget resolution (max_price) if missing.
    if int(state.get("tool_calls_this_turn") or 0) == 0:
        state = await _resolve_budget(state, llm=llm, user_msg=user_msg, today_utc=today_utc)
        if state.get("_end_turn"):
            return state

    # Step 1e: optional hard filters can be set/cleared on any turn.
    if int(state.get("tool_calls_this_turn") or 0) == 0:
        state = await _resolve_hard_filters_patch(state, llm=llm, user_msg=user_msg, today_utc=today_utc)

    # If tool-relevant constraints changed since the last completed tool run, drop cached tool results
    # so this turn re-runs the pipeline with the updated filters (e.g. min_star).
    cur_key = _tool_constraints_key(LLMConstraints.model_validate(state.get("constraints") or {}).model_dump(exclude_none=True))
    prev_key = state.get("tool_constraints_key")
    has_tool_cache = bool(
        state.get("candidates") or state.get("offers") or state.get("ranked_offers") or state.get("recommended_offers")
    )
    # Backward-compat: older snapshots did not persist tool_constraints_key, so cached tool state may be stale.
    # If we have cached tool results but no key, force a refresh once.
    if has_tool_cache and ("tool_constraints_key" not in state):
        logger.info("tool_state_invalidated", kind="missing_tool_constraints_key")
        # LangGraph state merges dicts; "pop" won't delete prior keys. Overwrite explicitly.
        state["candidates"] = []
        state["offers"] = []
        state["ranked_offers"] = []
        state["recommended_offers"] = []
        state["reasons"] = []
        state["tool_constraints_key"] = None
    if prev_key and prev_key != cur_key:
        logger.info("tool_state_invalidated", prev_key=prev_key, new_key=cur_key)
        state["candidates"] = []
        state["offers"] = []
        state["ranked_offers"] = []
        state["recommended_offers"] = []
        state["reasons"] = []
        state["tool_constraints_key"] = None

    # Step 2: decision (call tools or respond), using updated constraints.
    state_json["constraints"] = state.get("constraints") or {}
    if extracted_offer_id:
        state_json["offer_id_from_extract"] = extracted_offer_id

    # No-repricing workflow: if the user provided an offer_id, treat it as a selection.
    # Only confirm if we have tool-provided offers in the current session; otherwise, ask to rerun shopping.
    if extracted_offer_id:
        has_offer_context = bool(state.get("recommended_offers") or state.get("ranked_offers") or state.get("offers"))
        if has_offer_context:
            state["selected_offer_id"] = extracted_offer_id
            state["llm_action"] = {
                "type": "respond",
                "kind": "confirm",
                "message": "Confirm the selected offer using only tool-provided fields already present in session context.",
            }
        else:
            state["llm_action"] = {
                "type": "respond",
                "kind": "clarify",
                "message": "I don't have that offer loaded in this session. Do you want me to search again (same city/dates), or provide the city + dates + adults + rooms?",
            }
        return state

    prompt = DECIDE_TEMPLATE.format(user_message=user_msg, state_json=json.dumps(state_json, default=str))
    sys = SystemMessage(content=DECIDE_SYSTEM_PROMPT)
    human = HumanMessage(content=prompt)

    last_err: Exception | None = None
    for attempt in range(2):
        try:
            raw = (await llm.ainvoke([sys, human])).content  # type: ignore[attr-defined]
            obj = json.loads(_extract_first_json_object(str(raw)))
            action = AGENT_ACTION_ADAPTER.validate_python(obj)
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            # One correction attempt: ask for JSON only.
            human = HumanMessage(
                content=(
                    prompt
                    + "\n\nYour previous output was invalid. Return ONLY a single JSON object matching the schema. "
                    + "No markdown, no prose."
                )
            )
    else:
        FALLBACK_TOTAL.labels("model_error").inc()
        state["llm_action"] = {"type": "respond", "kind": "generic", "message": f"Model decision failed: {last_err}"}
        return state

    # Merge any constraints update from the model into state.
    if isinstance(action, AgentActionCallTool):
        state["constraints"] = _merge_constraints_dict(state.get("constraints") or {}, action.constraints_update)
        # Guardrail: keep tool execution on the strict pipeline.
        parsed = LLMConstraints.model_validate(state.get("constraints") or {}).model_dump(exclude_none=True)
        c = Constraints(**_tool_constraint_subset(parsed))
        expected_tool: str | None = None
        if c.is_complete():
            if not state.get("candidates"):
                expected_tool = "search_candidates"
            elif state.get("candidates") and not state.get("offers"):
                expected_tool = "get_offers"
            elif state.get("offers") and not state.get("ranked_offers"):
                expected_tool = "rank_offers"
            else:
                # Already have ranked offers; stop tool looping.
                state["llm_action"] = {"type": "respond", "kind": "explain", "message": "Show top offers."}
                return state

        if expected_tool and action.tool_name != expected_tool:
            FALLBACK_TOTAL.labels("tool_pipeline_override").inc()
            state["llm_action"] = {"type": "call_tool", "tool_name": expected_tool, "payload": {}}
            return state

        state["llm_action"] = action.model_dump()
        return state
    if isinstance(action, AgentActionRespond):
        prev_after_prelude = dict(state.get("constraints") or {})
        state["constraints"] = _merge_constraints_dict(state.get("constraints") or {}, action.constraints_update)
        state["llm_action"] = action.model_dump()
        if action.kind == "confirm" and extracted_offer_id:
            state["selected_offer_id"] = extracted_offer_id

        # Guardrail: if constraints are complete and we have not executed the tool pipeline,
        # do not allow the model to "respond" prematurely (this causes eval flakiness and tool-roleplay).
        parsed = LLMConstraints.model_validate(state.get("constraints") or {}).model_dump(exclude_none=True)
        c = Constraints(**_tool_constraint_subset(parsed))
        if c.is_complete():
            if not state.get("candidates"):
                state["llm_action"] = {"type": "call_tool", "tool_name": "search_candidates", "payload": {}}
                return state
            if state.get("candidates") and not state.get("offers"):
                state["llm_action"] = {"type": "call_tool", "tool_name": "get_offers", "payload": {}}
                return state
            if state.get("offers") and not state.get("ranked_offers"):
                state["llm_action"] = {"type": "call_tool", "tool_name": "rank_offers", "payload": {}}
                return state

        # If we are still missing required fields and the model didn't add any new constraints,
        # end early with a minimal clarification (avoid repetitive re-asking / long prompts).
        after = LLMConstraints.model_validate(state.get("constraints") or {}).model_dump(exclude_none=True)
        missing_now = _missing_required_fields(after)
        if action.kind == "clarify" and missing_now and prev_after_prelude == after:
            state["assistant_message"] = _clarify_message(missing_now, after)
            state["agent_state"] = "COLLECT_CONSTRAINTS"
            state["_end_turn"] = True
            return state

        return state

    state["llm_action"] = {"type": "respond", "kind": "generic", "message": "Unknown action type."}
    return state


def _route_after_llm_decide(state: GraphState) -> Literal["CALL_TOOL", "RESPOND", "END"]:
    if state.get("_end_turn"):
        return "END"
    action = state.get("llm_action") or {}
    t = action.get("type")
    if t == "call_tool":
        return "CALL_TOOL"
    if t == "respond":
        return "RESPOND"
    return "END"


def _route_after_call_tool(state: GraphState) -> Literal["DECIDE", "RESPOND"]:
    action = state.get("llm_action") or {}
    if action.get("type") == "respond":
        return "RESPOND"
    return "DECIDE"


async def _call_search_candidates(state: GraphState, *, tool: ToolClient, c: Constraints, parsed: dict[str, Any]) -> GraphState:
    if not c.is_complete():
        missing = _missing_required_fields(parsed)
        state["llm_action"] = {
            "type": "respond",
            "kind": "clarify",
            "message": _clarify_message(missing, parsed),
        }
        return state
    # search_candidates defines the hotel universe for this trip.
    # Any previously cached offers/rankings are invalid once we refresh candidates.
    state["offers"] = []
    state["ranked_offers"] = []
    state["recommended_offers"] = []
    state["reasons"] = []
    payload = c.to_tool_payload(SETTINGS.default_tenant_id)
    # Record the constraints that these candidates correspond to, so we can invalidate later.
    state["tool_constraints_key"] = _tool_constraints_key(parsed)
    result, evt = await tool.call("search_candidates", "/tools/search_candidates", payload)
    state.setdefault("tool_timeline", []).append(evt)
    state["candidates"] = result.get("candidates") or []
    if not state["candidates"]:
        # Stop tool looping; let the LLM propose a single constraint change next turn.
        state["llm_action"] = {
            "type": "respond",
            "kind": "generic",
            "message": "No candidate hotels matched these constraints.",
        }
    return state


async def _call_get_offers(state: GraphState, *, tool: ToolClient, c: Constraints, parsed: dict[str, Any]) -> GraphState:
    candidates = state.get("candidates") or []
    hotel_ids = [h["hotel_id"] for h in candidates[: SETTINGS.max_hotels_priced_per_turn]]
    if not hotel_ids or not c.check_in or not c.check_out:
        missing = _missing_required_fields(parsed)
        state["llm_action"] = {
            "type": "respond",
            "kind": "clarify",
            "message": _clarify_message(missing, parsed),
        }
        return state
    payload = {
        "tenant_id": SETTINGS.default_tenant_id,
        "hotel_ids": hotel_ids,
        "trip": {
            "check_in": c.check_in.isoformat(),
            "check_out": c.check_out.isoformat(),
            "occupancy": {"adults": c.adults, "children": c.children or 0, "rooms": c.rooms},
        },
        "currency": c.currency,
    }
    # Keep get_offers consistent with hard filters used in candidate selection.
    # This is required for refundable_only: a hotel can have both refundable and non-refundable offers.
    hf = c.hard_filters_payload()
    if hf:
        # get_offers only supports max_price/refundable_only today; extra keys are harmless (tools forbid extras),
        # so pass the same object for consistency.
        payload["hard_filters"] = hf
    result, evt = await tool.call("get_offers", "/tools/get_offers", payload)
    state.setdefault("tool_timeline", []).append(evt)
    state["offers"] = result.get("offers") or []
    return state


async def _call_rank_offers(state: GraphState, *, tool: ToolClient) -> GraphState:
    offers = state.get("offers") or []
    if not offers:
        state["llm_action"] = {
            "type": "respond",
            "kind": "generic",
            "message": "No offers available to rank.",
        }
        return state
    payload = {
        "offers": offers,
        "user_prefs": {"max_price": (state.get("constraints") or {}).get("max_price")},
        "objective_weights": {"price": 0.65, "refundable": 0.25, "freshness": 0.10},
    }
    result, evt = await tool.call("rank_offers", "/tools/rank_offers", payload)
    state.setdefault("tool_timeline", []).append(evt)
    state["ranked_offers"] = result.get("ranked_offers") or []
    state["reasons"] = result.get("reasons") or []
    # End the tool loop deterministically: once ranked, respond with the top offers.
    state["llm_action"] = {"type": "respond", "kind": "explain", "message": "Show top offers."}
    return state


async def _call_tool(state: GraphState) -> GraphState:
    state["agent_state"] = "CALL_TOOL"
    action = AgentActionCallTool.model_validate(state.get("llm_action") or {})
    tool_name = action.tool_name

    # Guardrail: tool call budget.
    used = int(state.get("tool_calls_this_turn") or 0)
    if used >= SETTINGS.max_tool_calls_per_turn:
        FALLBACK_TOTAL.labels("max_tool_calls").inc()
        state["llm_action"] = {
            "type": "respond",
            "kind": "generic",
            "message": "Tool call limit reached for this turn.",
        }
        return state
    state["tool_calls_this_turn"] = used + 1

    # Always re-derive tool payload from state (tool schema safety).
    # Parse constraints via Pydantic first so date strings round-trip correctly through JSONB.
    parsed = LLMConstraints.model_validate(state.get("constraints") or {}).model_dump(exclude_none=True)
    c = Constraints(**_tool_constraint_subset(parsed))
    tool = ToolClient(SETTINGS.tools_base_url)

    if tool_name == "search_candidates":
        return await _call_search_candidates(state, tool=tool, c=c, parsed=parsed)

    if tool_name == "get_offers":
        return await _call_get_offers(state, tool=tool, c=c, parsed=parsed)

    if tool_name == "rank_offers":
        return await _call_rank_offers(state, tool=tool)

    # Allowlist should prevent this, but keep a hard stop.
    state["llm_action"] = {"type": "respond", "kind": "generic", "message": f"Unknown tool: {tool_name}"}
    return state


def _build_offer_cards(state: GraphState) -> tuple[list[dict[str, Any]], list[float], list[datetime]]:
    candidates = {h["hotel_id"]: h for h in (state.get("candidates") or [])}
    ranked = state.get("ranked_offers") or []
    min_star = (state.get("constraints") or {}).get("min_star")
    if min_star is not None:
        # Defensive filter: even if cached results slip through, never display below-min_star hotels.
        try:
            ms = float(min_star)
        except Exception:  # noqa: BLE001
            ms = None
        if ms is not None:
            filtered = []
            for item in ranked:
                offer = item.get("offer") or {}
                h = candidates.get(offer.get("hotel_id")) or {}
                star = h.get("star_rating")
                try:
                    star_f = float(star) if star is not None else None
                except Exception:  # noqa: BLE001
                    star_f = None
                if star_f is not None and star_f >= ms:
                    filtered.append(item)
            ranked = filtered
    top = ranked[:3]

    cards: list[dict[str, Any]] = []
    allowed_prices: list[float] = []
    allowed_ts: list[datetime] = []
    for item in top:
        offer = item["offer"]
        h = candidates.get(offer["hotel_id"]) or {}
        card = {
            **offer,
            "hotel_name": h.get("name", "Unknown hotel"),
            "city": h.get("city"),
            "neighborhood": h.get("neighborhood"),
            "latitude": h.get("latitude"),
            "longitude": h.get("longitude"),
            "star_rating": h.get("star_rating"),
        }
        cards.append(card)
        allowed_prices.append(float(offer["total_price"]))
        # Also allow tool-provided components to avoid false positives when the LLM mentions them.
        allowed_prices.append(float(offer["taxes_total"]))
        allowed_prices.append(float(offer["fees_total"]))
        allowed_ts.append(_parse_dt(offer["last_priced_ts"]))
        allowed_ts.append(_parse_dt(offer["expires_ts"]))
        if offer.get("cancellation_deadline"):
            allowed_ts.append(_parse_dt(offer["cancellation_deadline"]))
    return cards, allowed_prices, allowed_ts


def _format_offer_card_lines(card: dict[str, Any]) -> str:
    # Tool-only fallback renderer (used only when LLM fails grounding).
    return "\n".join(
        [
            f"- offer_id: {card.get('offer_id')}",
            f"  hotel_id: {card.get('hotel_id')}",
            f"  hotel: {card.get('hotel_name', 'Unknown hotel')}",
            f"  star_rating: {card.get('star_rating')}",
            f"  total_price: ${float(card.get('total_price')):.2f}",
            f"  taxes_total: ${float(card.get('taxes_total')):.2f}",
            f"  fees_total: ${float(card.get('fees_total')):.2f}",
            f"  refundable: {card.get('refundable')}",
            f"  cancellation_deadline: {card.get('cancellation_deadline')}",
            f"  inventory_status: {card.get('inventory_status')}",
            f"  last_priced_ts: {card.get('last_priced_ts')}",
            f"  expires_ts: {card.get('expires_ts')}",
            f"  room_type: {card.get('room_type')}",
            f"  bed_config: {card.get('bed_config')}",
            f"  rate_plan: {card.get('rate_plan')}",
        ]
    )


def _render_top_offers_message(constraints: dict[str, Any], offers: list[dict[str, Any]]) -> str:
    """
    Deterministic (tool-only) offer list renderer.

    Goal: keep the top-3 offer display *consistent every time* regardless of LLM phrasing.
    """
    city = (constraints or {}).get("city") or "(city unknown)"
    ci = (constraints or {}).get("check_in") or "(check_in unknown)"
    co = (constraints or {}).get("check_out") or "(check_out unknown)"
    min_star = (constraints or {}).get("min_star")
    adults = (constraints or {}).get("adults")
    rooms = (constraints or {}).get("rooms")
    occ = []
    if adults is not None:
        occ.append(f"{adults} adult" + ("" if int(adults) == 1 else "s"))
    if rooms is not None:
        occ.append(f"{rooms} room" + ("" if int(rooms) == 1 else "s"))
    occ_s = (" • " + ", ".join(occ)) if occ else ""

    star_s = f" (min {min_star}-star)" if min_star is not None else ""
    header = f"Top 3 offers (tool-provided) for {city}{star_s} • {ci} to {co}{occ_s}:"
    blocks = []
    for i, o in enumerate(offers[:3], start=1):
        blocks.append(f"{i})\n" + _format_offer_card_lines(o))
    body = "\n\n".join(blocks) if blocks else "(no offers)"
    return header + "\n\n" + body + "\n\nSelect by replying with the offer_id."


def _render_selected_offer_message(constraints: dict[str, Any], offer: dict[str, Any]) -> str:
    city = (constraints or {}).get("city") or "(city unknown)"
    ci = (constraints or {}).get("check_in") or "(check_in unknown)"
    co = (constraints or {}).get("check_out") or "(check_out unknown)"
    return (
        f"Selected offer (tool-provided) for {city} • {ci} to {co}:\n\n"
        + _format_offer_card_lines(offer)
        + "\n\nReply with a different offer_id to choose another option."
    )


async def _llm_respond(state: GraphState) -> GraphState:
    # Internal node name; we set a stable external agent_state at the end.
    state["agent_state"] = "LLM_RESPOND"
    llm = get_chat_model()
    action = AgentActionRespond.model_validate(state.get("llm_action") or {"type": "respond"})

    user_msg = state.get("user_message", "")
    kind = action.kind

    # Build tool-grounded context for the response.
    parsed_constraints = LLMConstraints.model_validate(state.get("constraints") or {}).model_dump(exclude_none=True)
    missing_fields = _missing_required_fields(parsed_constraints)
    context: dict[str, Any] = {
        "constraints": state.get("constraints") or {},
        "missing_required_fields": missing_fields,
        "hint": action.message,
        "tool_timeline": state.get("tool_timeline") or [],
    }

    allowed_prices: list[float] = []
    allowed_ts: list[datetime] = []

    selected_offer_id = state.get("selected_offer_id")
    if kind == "confirm" or selected_offer_id:
        kind = "confirm"
        if selected_offer_id:
            context["selected_offer_id"] = selected_offer_id

        # Find selected offer from tool-provided session data.
        selected = _find_selected_offer(
            selected_offer_id,
            state.get("recommended_offers"),
            state.get("offers"),
            state.get("candidates"),
        )

        if not selected:
            # No grounded data to confirm; do not let the model guess.
            state["assistant_message"] = (
                "I couldn't find that offer_id in this session. "
                "Please reply with one of the offer_id values I listed, or ask me to search again."
            )
            state["agent_state"] = "WAIT_FOR_SELECTION"
            return state

        # Deterministic rendering for consistency.
        state["assistant_message"] = _render_selected_offer_message(state.get("constraints") or {}, selected)
        state["agent_state"] = "CONFIRM"
        return state
    elif state.get("ranked_offers"):
        kind = "explain"
        cards, allowed_prices, allowed_ts = _build_offer_cards(state)
        state["recommended_offers"] = cards
        context["offers"] = cards
        # Deterministic rendering for consistency.
        state["assistant_message"] = _render_top_offers_message(state.get("constraints") or {}, cards)
        state["agent_state"] = "WAIT_FOR_SELECTION"
        return state
    else:
        # Clarification / no-results path.
        context["candidates_n"] = len(state.get("candidates") or [])
        context["offers_n"] = len(state.get("offers") or [])
        context["ranked_offers_n"] = len(state.get("ranked_offers") or [])

    prompt = RESPOND_TEMPLATE.format(kind=kind, user_message=user_msg, context_json=json.dumps(context, default=str))
    sys = SystemMessage(content=SYSTEM_PROMPT)
    human = HumanMessage(content=prompt)

    class _MissingHotelId(ValueError):
        pass

    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            msg = (await llm.ainvoke([sys, human])).content  # type: ignore[attr-defined]
            msg = str(msg)
            if allowed_prices or allowed_ts:
                validate_grounded_response(msg, allowed_prices=allowed_prices, allowed_timestamps=allowed_ts)
            # Do not post-process model output. Enforce required IDs via retries only.
            if kind == "explain" and state.get("recommended_offers") and "hotel_id" not in msg.lower():
                raise _MissingHotelId("missing hotel_id in explain response")
            if kind == "confirm" and context.get("selected_offer") and "hotel_id" not in msg.lower():
                raise _MissingHotelId("missing hotel_id in confirm response")
            state["assistant_message"] = msg
            break
        except _MissingHotelId:
            FALLBACK_TOTAL.labels("missing_required_field").inc()
            human = HumanMessage(
                content=prompt
                + "\n\nYour response is missing required field hotel_id. "
                + "Rewrite and include hotel_id for each offer you list. "
                + "Do not add, remove, compute, or change any prices/timestamps."
            )
        except GroundingViolation:
            FALLBACK_TOTAL.labels("grounding_violation").inc()
            human = HumanMessage(
                content=prompt
                + "\n\nYour response included values not present in CONTEXT_JSON. "
                + "Rewrite using only tool-grounded values. Do not add or compute any prices/timestamps."
            )
        except Exception:  # noqa: BLE001
            FALLBACK_TOTAL.labels("model_error").inc()
            human = HumanMessage(content=prompt + "\n\nModel error. Try again with concise output.")
    else:
        # No tool-rendered fallbacks: if the model cannot comply after retries, return a short error.
        state["assistant_message"] = (
            "I couldn't produce a valid response (missing required fields or grounding). "
            "Please retry your last message."
        )

    # Output guardrail: when we present offers, always include a clear selection instruction.
    if kind == "explain" and state.get("recommended_offers"):
        msg = state.get("assistant_message") or ""
        if "offer_id" not in msg.lower():
            state["assistant_message"] = msg.rstrip() + "\n\nSelect by replying with the offer_id."

    # External/stable agent_state for API/UI compatibility.
    if kind == "explain" and state.get("recommended_offers"):
        state["agent_state"] = "WAIT_FOR_SELECTION"
    elif kind == "confirm":
        state["agent_state"] = "CONFIRM"
    elif kind == "clarify":
        state["agent_state"] = "COLLECT_CONSTRAINTS"
    else:
        state["agent_state"] = "RESPOND"

    # Selection bookkeeping: keep the last selection for UI/debug, but clear control-flow selection.
    if state.get("selected_offer_id"):
        state["last_selected_offer_id"] = state.get("selected_offer_id")
        state.pop("selected_offer_id", None)
    return state
