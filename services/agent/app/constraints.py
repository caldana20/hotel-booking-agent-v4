from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass
class Constraints:
    city: str | None = None
    check_in: date | None = None
    check_out: date | None = None
    adults: int | None = None
    children: int | None = None
    rooms: int | None = None
    max_price: float | None = None
    min_star: float | None = None
    amenities: list[str] | None = None
    # Treated as a hard filter when true (tools enforce refundable_only).
    refundable_preferred: bool | None = None
    currency: str = "USD"

    def is_complete(self) -> bool:
        return bool(self.city and self.check_in and self.check_out and self.adults and self.rooms)

    def hard_filters_payload(self) -> dict[str, Any] | None:
        """
        Build the tools `hard_filters` payload (or None).

        Centralizing this avoids subtle drift between search_candidates and get_offers.
        """
        hard_filters: dict[str, Any] = {}
        if self.max_price:
            hard_filters["max_price"] = self.max_price
        if self.min_star:
            hard_filters["min_star"] = self.min_star
        if self.amenities:
            hard_filters["amenities"] = self.amenities
        if self.refundable_preferred:
            hard_filters["refundable_only"] = True
        return hard_filters or None

    def to_tool_payload(self, tenant_id: str) -> dict[str, Any]:
        assert self.city and self.check_in and self.check_out and self.adults and self.rooms
        return {
            "tenant_id": tenant_id,
            "location": {"city": self.city},
            "check_in": self.check_in.isoformat(),
            "check_out": self.check_out.isoformat(),
            "occupancy": {"adults": self.adults, "children": self.children or 0, "rooms": self.rooms},
            "hard_filters": self.hard_filters_payload(),
        }


"""
Note: This module intentionally does NOT parse free-form user text.
All user-input understanding is delegated to the LLM, which emits structured constraints.
"""

