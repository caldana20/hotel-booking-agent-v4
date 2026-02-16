from __future__ import annotations

from datetime import datetime
from typing import Any

from services.agent.app.llm_schemas import LLMConstraints


def missing_required_fields(constraints: dict[str, Any]) -> list[str]:
    c = constraints or {}
    missing: list[str] = []
    if not c.get("city"):
        missing.append("city")
    if not c.get("check_in") or not c.get("check_out"):
        missing.append("dates")
    if not c.get("adults"):
        missing.append("adults")
    if not c.get("rooms"):
        missing.append("rooms")
    return missing


def clarify_message(missing: list[str], constraints: dict[str, Any]) -> str:
    """
    Minimal, non-assumptive clarification. Keep to 1-2 short questions.
    """
    city = (constraints or {}).get("city")
    ci = (constraints or {}).get("check_in")
    co = (constraints or {}).get("check_out")

    if missing == ["adults", "rooms"] and city and ci and co:
        return f"I can search {city} for {ci} to {co}. How many adults and rooms?"
    if "dates" in missing and city:
        return f"What dates should I use for {city}? (YYYY-MM-DD to YYYY-MM-DD)"
    if "city" in missing and "dates" in missing:
        return "Which city and dates? (Example: Austin, 2026-03-10 to 2026-03-12)"
    if "city" in missing:
        return "Which city should I search in?"
    if "dates" in missing:
        return "What are your check-in and check-out dates? (YYYY-MM-DD to YYYY-MM-DD)"
    if "adults" in missing and "rooms" in missing:
        return "How many adults and rooms?"
    if "adults" in missing:
        return "How many adults?"
    if "rooms" in missing:
        return "How many rooms?"
    return "What details should I use to continue?"


def merge_constraints_dict(base: dict[str, Any], update: LLMConstraints | None) -> dict[str, Any]:
    if not update:
        return base
    out = dict(base or {})
    upd = update.model_dump(exclude_none=True)
    for k, v in upd.items():
        out[k] = v
    return out


def tool_constraint_subset(d: dict[str, Any]) -> dict[str, Any]:
    tool_constraint_keys = {
        "city",
        "check_in",
        "check_out",
        "adults",
        "children",
        "rooms",
        "max_price",
        "min_star",
        "amenities",
        "refundable_preferred",
        "currency",
    }
    return {k: v for k, v in (d or {}).items() if k in tool_constraint_keys}


def extract_first_json_object(text: str) -> str:
    """
    Models sometimes wrap JSON in markdown or include commentary.
    Extract the first {...} block by naive brace scanning.
    """
    s = text.strip()
    if s.startswith("{") and s.endswith("}"):
        return s
    start = s.find("{")
    if start < 0:
        raise ValueError("No JSON object found in model output.")
    depth = 0
    for i in range(start, len(s)):
        ch = s[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    raise ValueError("Unterminated JSON object in model output.")


def parse_dt(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v
    # v is usually an ISO string from tool API
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def find_selected_offer(
    selected_offer_id: str | None,
    recommended_offers: list[dict[str, Any]] | None,
    offers: list[dict[str, Any]] | None,
    candidates: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """
    Locate a selected offer strictly from tool-provided session data.

    Order of preference matches the existing runtime behavior:
    1) recommended_offers (already decorated and shown to user)
    2) offers list (decorate with candidate hotel fields if possible)
    """
    if not selected_offer_id:
        return None

    for c in (recommended_offers or []):
        if str(c.get("offer_id")) == str(selected_offer_id):
            return c

    target: dict[str, Any] | None = None
    for o in (offers or []):
        if str(o.get("offer_id")) == str(selected_offer_id):
            target = o
            break
    if not target:
        return None

    hotel_id = target.get("hotel_id")
    h: dict[str, Any] | None = None
    for cand in (candidates or []):
        if cand.get("hotel_id") == hotel_id:
            h = cand
            break

    return {
        **target,
        "hotel_name": (h or {}).get("name", "Unknown hotel"),
        "city": (h or {}).get("city"),
        "neighborhood": (h or {}).get("neighborhood"),
        "latitude": (h or {}).get("latitude"),
        "longitude": (h or {}).get("longitude"),
        "star_rating": (h or {}).get("star_rating"),
    }

