from __future__ import annotations

import pytest


def test_extract_first_json_object_raw_json() -> None:
    from services.agent.app.graph_helpers import extract_first_json_object

    assert extract_first_json_object('{"a": 1}') == '{"a": 1}'


def test_extract_first_json_object_markdown_wrapped() -> None:
    from services.agent.app.graph_helpers import extract_first_json_object

    text = "```json\n{\"a\": 1, \"b\": {\"c\": 2}}\n```"
    assert extract_first_json_object(text) == '{"a": 1, "b": {"c": 2}}'


def test_extract_first_json_object_with_prose() -> None:
    from services.agent.app.graph_helpers import extract_first_json_object

    text = 'Sure. Here is the JSON:\n\n{"ok": true}\n\nThanks.'
    assert extract_first_json_object(text) == '{"ok": true}'


def test_extract_first_json_object_unterminated_raises() -> None:
    from services.agent.app.graph_helpers import extract_first_json_object

    with pytest.raises(ValueError, match="Unterminated JSON object"):
        extract_first_json_object('prefix {"a": 1 ')


def test_extract_first_json_object_missing_raises() -> None:
    from services.agent.app.graph_helpers import extract_first_json_object

    with pytest.raises(ValueError, match="No JSON object found"):
        extract_first_json_object("no braces here")


def test_merge_constraints_dict_overwrites_and_preserves() -> None:
    from services.agent.app.graph_helpers import merge_constraints_dict
    from services.agent.app.llm_schemas import LLMConstraints

    base = {"city": "Austin", "max_price": 250}
    update = LLMConstraints(city="Dallas", adults=2)
    out = merge_constraints_dict(base, update)

    assert out["city"] == "Dallas"
    assert out["adults"] == 2
    assert out["max_price"] == 250


def test_missing_required_fields_and_clarify_message_minimal() -> None:
    from services.agent.app.graph_helpers import clarify_message, missing_required_fields

    constraints = {"city": "Austin", "check_in": "2026-03-10", "check_out": "2026-03-12"}
    missing = missing_required_fields(constraints)
    assert missing == ["adults", "rooms"]
    msg = clarify_message(missing, constraints)
    assert "How many adults and rooms?" in msg


def test_find_selected_offer_prefers_recommended_offer() -> None:
    from services.agent.app.graph_helpers import find_selected_offer

    selected_offer_id = "offer-1"
    recommended = [{"offer_id": "offer-1", "hotel_name": "Rec Hotel", "hotel_id": "h1"}]
    offers = [{"offer_id": "offer-1", "hotel_id": "h2", "total_price": 1.0}]
    candidates = [{"hotel_id": "h2", "name": "Cand Hotel"}]

    selected = find_selected_offer(selected_offer_id, recommended, offers, candidates)
    assert selected is not None
    assert selected.get("hotel_name") == "Rec Hotel"


def test_find_selected_offer_decorates_from_candidates() -> None:
    from services.agent.app.graph_helpers import find_selected_offer

    selected_offer_id = "offer-2"
    recommended = []
    offers = [{"offer_id": "offer-2", "hotel_id": "h2", "total_price": 123.45}]
    candidates = [{"hotel_id": "h2", "name": "Cand Hotel", "city": "Austin"}]

    selected = find_selected_offer(selected_offer_id, recommended, offers, candidates)
    assert selected is not None
    assert selected.get("hotel_name") == "Cand Hotel"
    assert selected.get("city") == "Austin"


def test_find_selected_offer_missing_returns_none() -> None:
    from services.agent.app.graph_helpers import find_selected_offer

    selected = find_selected_offer("missing", [], [], [])
    assert selected is None

