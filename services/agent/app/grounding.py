from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable


_MONEY_RE = re.compile(r"(?<!\w)\$([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?)")
_ISO_TS_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))\b")


class GroundingViolation(ValueError):
    pass


def validate_grounded_response(
    assistant_text: str,
    allowed_prices: Iterable[float],
    allowed_timestamps: Iterable[datetime],
) -> None:
    """
    MVP guardrail: do not let the agent invent monetary values or timestamps.

    - Any $X.YY appearing must match one of the tool-derived prices.
    - Any ISO timestamps must match one of the tool-derived timestamps.

    This is intentionally strict; it encourages the agent to use tool outputs verbatim.
    """
    allowed_price_set = {f"{p:.2f}" for p in allowed_prices}
    for m in _MONEY_RE.finditer(assistant_text):
        v = m.group(1).replace(",", "")
        if v not in allowed_price_set:
            raise GroundingViolation(f"ungrounded price ${m.group(1)}")

    allowed_ts_set: set[str] = set()
    for t in allowed_timestamps:
        s = t.isoformat()
        allowed_ts_set.add(s)
        # Accept equivalent UTC forms: "+00:00" and "Z"
        if s.endswith("+00:00"):
            allowed_ts_set.add(s[: -6] + "Z")
    for m in _ISO_TS_RE.finditer(assistant_text):
        if m.group(1) not in allowed_ts_set:
            raise GroundingViolation(f"ungrounded timestamp {m.group(1)}")

