from __future__ import annotations


def test_truncate_json_bounds_long_strings() -> None:
    from services.agent.app.tool_client import _truncate_json

    out = _truncate_json({"s": "x" * 5000}, max_str=100)
    assert isinstance(out, dict)
    assert out["s"].endswith("...__truncated__")


def test_truncate_json_bounds_lists_and_appends_len_marker() -> None:
    from services.agent.app.tool_client import _truncate_json

    out = _truncate_json({"xs": list(range(100))}, max_list=10)
    xs = out["xs"]
    assert len(xs) == 11
    assert str(xs[-1]).startswith("__truncated_list_len=")


def test_truncate_json_depth_limit() -> None:
    from services.agent.app.tool_client import _truncate_json

    obj = {"a": {"b": {"c": {"d": 1}}}}
    out = _truncate_json(obj, max_depth=2)
    assert out["a"]["b"] == "__truncated_depth__"


def test_result_counts_per_tool() -> None:
    from services.agent.app.tool_client import _result_counts

    assert _result_counts("search_candidates", {"candidates": [1, 2]}) == {"candidates": 2}
    assert _result_counts("get_offers", {"offers": [1]}) == {"offers": 1}
    assert _result_counts("rank_offers", {"ranked_offers": [1, 2, 3]}) == {"ranked_offers": 3}
    assert _result_counts("other", {}) is None


def test_result_preview_is_small_and_consistent() -> None:
    from services.agent.app.tool_client import _result_preview

    prev = _result_preview(
        "search_candidates",
        {"candidates": [{"hotel_id": "h1", "name": "n1", "city": "c1", "neighborhood": "n"}], "counts": {"x": 1}},
    )
    assert prev is not None
    assert "candidates_top" in prev

