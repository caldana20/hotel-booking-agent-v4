from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import httpx
import re

from evals.metrics import (
    EvalResult,
    check_grounding_no_invented_prices,
    check_selection_fallback_message,
    check_tool_order,
)


def _load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


async def run_session(base_url: str, name: str, turns: list[dict[str, Any]]) -> EvalResult:
    session_id = None
    failures: list[str] = []
    last_recommended_prices: list[float] = []
    last_recommended_offer_id: str | None = None

    # Live LLM calls can be spiky; keep eval runner tolerant while still bounded.
    async with httpx.AsyncClient(base_url=base_url, timeout=60.0) as client:
        for t in turns:
            msg = _resolve_macros(t["message"])
            if msg == "__SELECT_FIRST_OFFER__":
                if not last_recommended_offer_id:
                    failures.append("select_first_offer_but_no_offer_id_seen")
                    break
                msg = f"I choose {last_recommended_offer_id}"

            try:
                r = await client.post("/chat", json={"session_id": session_id, "user_id": "eval-user", "message": msg})
            except httpx.TimeoutException:
                failures.append("chat_timeout")
                break
            except httpx.ReadError:
                # Server closed connection / transient network hiccup; record as failure rather than crashing eval run.
                failures.append("chat_read_error")
                break
            if r.status_code != 200:
                failures.append(f"chat_http_{r.status_code}")
                break

            data = r.json()
            session_id = data["session_id"]
            agent_state = data["agent_state"]
            assistant_message = data["assistant_message"]
            tool_timeline = data.get("tool_timeline") or []
            recs = data.get("recommended_offers") or []

            # Update allowed prices for grounding checks
            last_recommended_prices = [float(o["total_price"]) for o in recs[:3] if "total_price" in o]
            if recs:
                last_recommended_offer_id = str(recs[0].get("offer_id"))

            # Checks per turn
            # Only require selection instructions when there are selectable offers.
            if recs:
                failures += check_selection_fallback_message(agent_state, assistant_message)
            if tool_timeline:
                failures += check_tool_order(tool_timeline)[:]

            failures += check_grounding_no_invented_prices(assistant_message, last_recommended_prices)

            # Optional per-turn assertions from cases (not used by golden sessions unless added).
            failures += await _check_turn_assertions(client, session_id, t.get("assert") or {}, agent_state, assistant_message)

    return EvalResult(session_name=name, passed=(len(failures) == 0), failures=failures)


async def _check_turn_assertions(
    client: httpx.AsyncClient,
    session_id: str | None,
    assert_block: dict[str, Any],
    agent_state: str,
    assistant_message: str,
) -> list[str]:
    """
    Lightweight eval assertions to keep behavior from regressing without requiring
    exact LLM phrasing.
    """
    if not assert_block:
        return []
    failures: list[str] = []
    text = (assistant_message or "").lower()

    must_contain = [str(x).lower() for x in (assert_block.get("contains") or [])]
    for s in must_contain:
        if s not in text:
            failures.append(f"assert_contains_missing:{s}")

    must_not_contain = [str(x).lower() for x in (assert_block.get("not_contains") or [])]
    for s in must_not_contain:
        if s in text:
            failures.append(f"assert_not_contains_present:{s}")

    for pat in (assert_block.get("regex") or []):
        if not re.search(str(pat), assistant_message or "", flags=re.IGNORECASE):
            failures.append(f"assert_regex_missing:{pat}")

    for pat in (assert_block.get("not_regex") or []):
        if re.search(str(pat), assistant_message or "", flags=re.IGNORECASE):
            failures.append(f"assert_not_regex_present:{pat}")

    expected_state = assert_block.get("agent_state")
    if expected_state and agent_state != expected_state:
        failures.append(f"assert_agent_state_expected:{expected_state} actual:{agent_state}")

    # Optional constraints checks (pull from /sessions/{id}).
    if assert_block.get("constraints") and session_id:
        try:
            r = await client.get(f"/sessions/{session_id}")
            if r.status_code == 200:
                detail = r.json()
                constraints = detail.get("constraints") or {}
                cassert = assert_block.get("constraints") or {}
                # Example: {"required_keys": ["check_in","check_out"], "stay_len_days": 7}
                for k in cassert.get("required_keys") or []:
                    if k not in constraints or constraints.get(k) in (None, ""):
                        failures.append(f"assert_constraints_missing:{k}")
                if "stay_len_days" in cassert and constraints.get("check_in") and constraints.get("check_out"):
                    from datetime import date

                    ci = date.fromisoformat(constraints["check_in"])
                    co = date.fromisoformat(constraints["check_out"])
                    got = (co - ci).days
                    want = int(cassert["stay_len_days"])
                    if got != want:
                        failures.append(f"assert_stay_len_days_expected:{want} actual:{got}")
        except Exception:  # noqa: BLE001
            failures.append("assert_constraints_fetch_failed")

    return failures

def _resolve_macros(msg: str) -> str:
    import re
    from datetime import date, timedelta

    def repl(m: re.Match[str]) -> str:
        days = int(m.group(1))
        return (date.today() + timedelta(days=days)).isoformat()

    return re.sub(r"__TODAY_PLUS_(\d+)__", repl, msg)


async def amain() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--golden", required=True)
    p.add_argument("--cases", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    golden = _load_json(args.golden)
    cases = _load_json(args.cases)
    sessions = list(golden) + list(cases)

    results: list[dict[str, Any]] = []
    passed = 0
    for s in sessions:
        res = await run_session(args.base_url, s["name"], s["turns"])
        results.append({"name": res.session_name, "passed": res.passed, "failures": res.failures})
        passed += 1 if res.passed else 0

    out = {"total": len(results), "passed": passed, "results": results}
    Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    return 0 if passed == len(results) else 1


def main() -> None:
    import asyncio

    raise SystemExit(asyncio.run(amain()))


if __name__ == "__main__":
    main()

