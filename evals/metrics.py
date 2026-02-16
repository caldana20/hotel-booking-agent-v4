from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


MONEY_RE = re.compile(r"(?<!\\w)\\$([0-9]{1,3}(?:,[0-9]{3})*(?:\\.[0-9]{2})?)")


@dataclass
class EvalResult:
    session_name: str
    passed: bool
    failures: list[str]


def check_tool_order(tool_timeline: list[dict[str, Any]]) -> list[str]:
    names = [e.get("tool_name") for e in tool_timeline]
    needed = ["search_candidates", "get_offers", "rank_offers"]
    # If the agent stops early (e.g. no candidates), don't enforce the full pipeline order.
    if len(names) < 3:
        return []
    if names[:3] != needed:
        return [f"tool_order_expected_prefix={needed} actual_prefix={names[:3]}"]
    return []


def check_grounding_no_invented_prices(assistant_message: str, allowed_prices: list[float]) -> list[str]:
    allowed = {f"{p:.2f}" for p in allowed_prices}
    failures = []
    for m in MONEY_RE.finditer(assistant_message):
        v = m.group(1).replace(",", "")
        if v not in allowed:
            failures.append(f"ungrounded_price=${m.group(1)}")
    return failures


def check_selection_fallback_message(agent_state: str, assistant_message: str) -> list[str]:
    # Lightweight check: if agent is waiting (or confirming), it should provide a clear next step.
    if agent_state == "WAIT_FOR_SELECTION" and "offer_id" not in assistant_message:
        return ["missing_offer_id_instruction"]
    return []

