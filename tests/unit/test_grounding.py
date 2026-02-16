from __future__ import annotations

from datetime import datetime

import pytest


def test_validate_grounded_response_allows_known_price() -> None:
    from services.agent.app.grounding import validate_grounded_response

    validate_grounded_response("Total is $123.45", allowed_prices=[123.45], allowed_timestamps=[])


def test_validate_grounded_response_rejects_unknown_price() -> None:
    from services.agent.app.grounding import GroundingViolation, validate_grounded_response

    with pytest.raises(GroundingViolation, match="ungrounded price"):
        validate_grounded_response("Total is $123.46", allowed_prices=[123.45], allowed_timestamps=[])


def test_validate_grounded_response_accepts_z_and_plus00_equivalence() -> None:
    from services.agent.app.grounding import validate_grounded_response

    allowed = [datetime.fromisoformat("2026-02-15T12:00:00+00:00")]
    validate_grounded_response(
        "Expires at 2026-02-15T12:00:00Z",
        allowed_prices=[],
        allowed_timestamps=allowed,
    )

