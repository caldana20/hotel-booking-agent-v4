from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import AIMessage


def _extract_first_json_object(text: str) -> str:
    s = text.strip()
    if s.startswith("{") and s.endswith("}"):
        return s
    start = s.find("{")
    if start < 0:
        raise ValueError("No JSON object found.")
    depth = 0
    for i in range(start, len(s)):
        ch = s[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    raise ValueError("Unterminated JSON object.")


UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")


class ChatOpenAIStub:
    """
    Test-only stub for ChatOpenAI used to keep tests deterministic/offline.
    It understands the agent's internal DECIDE/RESPOND prompts.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        pass

    async def ainvoke(self, input: Any, **kwargs: Any) -> AIMessage:  # noqa: ANN401,A002
        # LangChain typically passes a list of messages; we look at the last message content.
        if isinstance(input, str):
            return AIMessage(content=input)

        try:
            last = input[-1].content  # type: ignore[index]
        except Exception:  # noqa: BLE001
            return AIMessage(content="OK")

        text = str(last)

        if "MODE:EXTRACT" in text:
            # Best-effort slot extraction for tests (deterministic/offline).
            m_msg = re.search(r"^User message:\s*(.*)$", text, flags=re.MULTILINE)
            user_message = m_msg.group(1).strip() if m_msg else ""

            extracted: dict[str, Any] = {}
            for city in ("Austin", "San Diego", "Chicago", "Seattle"):
                if city.lower() in user_message.lower():
                    extracted["city"] = city
                    break

            iso = re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", user_message)
            if len(iso) >= 2:
                extracted["check_in"] = iso[0]
                extracted["check_out"] = iso[1]

            m_adults = re.search(r"(\d+)\s+adults?\b", user_message.lower())
            if m_adults:
                extracted["adults"] = int(m_adults.group(1))
            m_rooms = re.search(r"(\d+)\s+rooms?\b", user_message.lower())
            if m_rooms:
                extracted["rooms"] = int(m_rooms.group(1))

            m_budget = re.search(r"(?:under|below|max)\s*\$?\s*(\d+(?:\.\d+)?)", user_message.lower())
            if m_budget:
                extracted["max_price"] = float(m_budget.group(1))

            m_uuid = UUID_RE.search(user_message)
            offer_id = m_uuid.group(0) if m_uuid else None

            return AIMessage(content=json.dumps({"constraints_update": (extracted or None), "offer_id": offer_id}))

        if "MODE:DATE_RESOLVE" in text:
            # Deterministic/offline date resolver for tests.
            try:
                state_txt = text.split("STATE_JSON:", 1)[1]
                state = json.loads(_extract_first_json_object(state_txt))
            except Exception:  # noqa: BLE001
                state = {}
            today_s = (state.get("today_utc") or "2026-01-01")
            from datetime import date, timedelta

            today = date.fromisoformat(today_s)
            m_msg = re.search(r"^User message:\s*(.*)$", text, flags=re.MULTILINE)
            user_message = (m_msg.group(1).strip() if m_msg else "").lower()

            # Very small heuristic for tests only.
            if "next week" in user_message:
                check_in = today + timedelta(days=7)
                stay = 3 if ("3 day" in user_message or "3-night" in user_message or "3 night" in user_message) else 2
                check_out = check_in + timedelta(days=stay)
                return AIMessage(content=json.dumps({"check_in": check_in.isoformat(), "check_out": check_out.isoformat()}))

            return AIMessage(content=json.dumps({"needs_clarification": True, "question": "What dates should I use? (YYYY-MM-DD to YYYY-MM-DD)"}))

        if "MODE:CITY_RESOLVE" in text:
            m_msg = re.search(r"^User message:\s*(.*)$", text, flags=re.MULTILINE)
            user_message = (m_msg.group(1).strip() if m_msg else "").lower()
            for city in ("austin", "san diego", "chicago", "seattle"):
                if city in user_message:
                    return AIMessage(content=json.dumps({"city": city.title() if city != "san diego" else "San Diego"}))
            return AIMessage(content=json.dumps({"needs_clarification": True, "question": "Which city should I search in?"}))

        if "MODE:OCCUPANCY_RESOLVE" in text:
            m_msg = re.search(r"^User message:\s*(.*)$", text, flags=re.MULTILINE)
            user_message = (m_msg.group(1).strip() if m_msg else "").lower()
            adults = 1 if ("one adult" in user_message or "1 adult" in user_message) else None
            rooms = 1 if ("one room" in user_message or "1 room" in user_message or "and room" in user_message) else None
            if adults and rooms:
                return AIMessage(content=json.dumps({"adults": adults, "rooms": rooms, "children": 0}))
            if adults and not rooms:
                return AIMessage(content=json.dumps({"needs_clarification": True, "question": "How many rooms?"}))
            if rooms and not adults:
                return AIMessage(content=json.dumps({"needs_clarification": True, "question": "How many adults?"}))
            return AIMessage(content=json.dumps({"needs_clarification": True, "question": "How many adults and rooms?"}))

        if "MODE:AMENITIES_RESOLVE" in text:
            m_msg = re.search(r"^User message:\s*(.*)$", text, flags=re.MULTILINE)
            user_message = (m_msg.group(1).strip() if m_msg else "").lower()
            am = []
            for k in ("wifi", "gym", "parking", "pool", "spa", "restaurant", "bar", "pet"):
                if k in user_message:
                    am.append("pet_friendly" if k == "pet" else k)
            refundable = True if ("refundable" in user_message or "free cancellation" in user_message) else None
            return AIMessage(content=json.dumps({"amenities": (am or None), "refundable_preferred": refundable}))

        if "MODE:BUDGET_RESOLVE" in text:
            m_msg = re.search(r"^User message:\s*(.*)$", text, flags=re.MULTILINE)
            user_message = (m_msg.group(1).strip() if m_msg else "").lower()
            m = re.search(r"(?:under|below|max)\\s*\\$?\\s*(\\d+(?:\\.\\d+)?)", user_message)
            if m:
                return AIMessage(content=json.dumps({"max_price": float(m.group(1))}))
            return AIMessage(content=json.dumps({"max_price": None}))

        if "MODE:HARD_FILTERS_RESOLVE" in text:
            m_msg = re.search(r"^User message:\s*(.*)$", text, flags=re.MULTILINE)
            user_message = (m_msg.group(1).strip() if m_msg else "").lower()
            set_patch: dict[str, Any] = {}
            clear: list[str] = []

            # Star rating updates
            if ("only five" in user_message) or ("5 star" in user_message) or ("five star" in user_message):
                set_patch["min_star"] = 5.0
            elif ("more than four" in user_message) or ("over four" in user_message) or ("4 star" in user_message) or ("four star" in user_message):
                # Treat as 4-star and up for tests.
                set_patch["min_star"] = 4.0
            elif ("no star" in user_message) or ("any star" in user_message):
                clear.append("min_star")

            # Refundable updates
            if ("must be refundable" in user_message) or ("refundable only" in user_message) or ("free cancellation required" in user_message):
                set_patch["refundable_preferred"] = True
            if ("doesn't need" in user_message and "refundable" in user_message) or ("not refundable" in user_message):
                clear.append("refundable_preferred")

            # Budget updates/removals
            if ("ignore budget" in user_message) or ("no budget" in user_message):
                clear.append("max_price")
            m = re.search(r"(?:under|below|max)\\s*\\$?\\s*(\\d+(?:\\.\\d+)?)", user_message)
            if m:
                set_patch["max_price"] = float(m.group(1))

            # Amenities updates (very light heuristic for tests).
            am = []
            for k in ("wifi", "gym", "parking", "pool", "spa", "restaurant", "bar"):
                if k in user_message:
                    am.append(k)
            if "pet" in user_message:
                am.append("pet_friendly")
            if "no amenities" in user_message:
                clear.append("amenities")
            elif am:
                set_patch["amenities"] = sorted(list(dict.fromkeys(am)))

            out = {"set": (set_patch or None), "clear": clear}
            return AIMessage(content=json.dumps(out))

        if "MODE:DECIDE" in text:
            try:
                state_txt = text.split("STATE_JSON:", 1)[1]
                state = json.loads(_extract_first_json_object(state_txt))
            except Exception:  # noqa: BLE001
                state = {}

            # Pull user message from the DECIDE template.
            m_msg = re.search(r"^User message:\s*(.*)$", text, flags=re.MULTILINE)
            user_message = m_msg.group(1).strip() if m_msg else ""

            extracted: dict[str, Any] = {}
            for city in ("Austin", "San Diego", "Chicago", "Seattle"):
                if city.lower() in user_message.lower():
                    extracted["city"] = city
                    break
            iso = re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", user_message)
            if len(iso) >= 2:
                extracted["check_in"] = iso[0]
                extracted["check_out"] = iso[1]
            m_adults = re.search(r"(\d+)\s+adults?\b", user_message.lower())
            if m_adults:
                extracted["adults"] = int(m_adults.group(1))
            m_rooms = re.search(r"(\d+)\s+rooms?\b", user_message.lower())
            if m_rooms:
                extracted["rooms"] = int(m_rooms.group(1))

            extracted.setdefault("adults", 2)
            extracted.setdefault("children", 0)
            extracted.setdefault("rooms", 1)

            selected = state.get("selected_offer_id")
            has_candidates = bool(state.get("has_candidates"))
            has_offers = bool(state.get("has_offers"))
            has_ranked = bool(state.get("has_ranked_offers"))

            complete = bool(
                extracted.get("city")
                and extracted.get("check_in")
                and extracted.get("check_out")
                and extracted.get("adults")
                and extracted.get("rooms")
            )

            chosen_uuid = None
            m_uuid = UUID_RE.search(user_message)
            if m_uuid:
                chosen_uuid = m_uuid.group(0)

            if (selected or chosen_uuid):
                action = {
                    "type": "respond",
                    "kind": "confirm",
                    "message": "Confirm selected offer using tool-provided fields from session context.",
                    "constraints_update": extracted or None,
                }
            elif not complete:
                action = {
                    "type": "respond",
                    "kind": "clarify",
                    "message": "Please provide city and dates and occupancy.",
                    "constraints_update": extracted or None,
                }
            elif not has_candidates:
                action = {"type": "call_tool", "tool_name": "search_candidates", "payload": {}, "constraints_update": extracted or None}
            elif has_candidates and not has_offers:
                action = {"type": "call_tool", "tool_name": "get_offers", "payload": {}, "constraints_update": extracted or None}
            elif has_offers and not has_ranked:
                action = {"type": "call_tool", "tool_name": "rank_offers", "payload": {}, "constraints_update": extracted or None}
            else:
                action = {"type": "respond", "kind": "explain", "message": "Show top offers.", "constraints_update": extracted or None}

            return AIMessage(content=json.dumps(action))

        if "MODE:RESPOND" in text:
            try:
                ctx_txt = text.split("CONTEXT_JSON:", 1)[1]
                ctx = json.loads(_extract_first_json_object(ctx_txt))
            except Exception:  # noqa: BLE001
                ctx = {}

            if ctx.get("selected_offer"):
                so = ctx.get("selected_offer") or {}
                offer_id = ctx.get("selected_offer_id") or so.get("offer_id") or "(unknown)"
                total = so.get("total_price")
                return AIMessage(content=f"Selected {offer_id} total_price={total}")

            offers = ctx.get("offers") or []
            if offers:
                lines = []
                for o in offers[:3]:
                    lines.append(f"- offer_id: {o.get('offer_id')} total_price: {o.get('total_price')}")
                lines.append("Select by replying with the offer_id.")
                return AIMessage(content="\n".join(lines))

            return AIMessage(content=str(ctx.get("hint") or "OK"))

        return AIMessage(content="OK")

