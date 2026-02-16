from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest

from evals.metrics import check_grounding_no_invented_prices, check_tool_order


REPO_ROOT = Path(__file__).resolve().parents[2]

def _resolve_macros(msg: str) -> str:
    # Supports tokens like __TODAY_PLUS_24__ in golden sessions.
    import re
    from datetime import date, timedelta

    def repl(m: re.Match[str]) -> str:
        days = int(m.group(1))
        return (date.today() + timedelta(days=days)).isoformat()

    return re.sub(r"__TODAY_PLUS_(\d+)__", repl, msg)


@pytest.fixture()
def tools_app(migrated_seeded_db: str):
    os.environ["DATABASE_URL"] = migrated_seeded_db
    os.environ["DEFAULT_TENANT_ID"] = "t_default"
    from services.tools.app.main import app

    return app


@pytest.fixture()
def agent_app(migrated_seeded_db: str, tools_app):
    os.environ["DATABASE_URL"] = migrated_seeded_db
    os.environ["DEFAULT_TENANT_ID"] = "t_default"
    os.environ["TOOLS_BASE_URL"] = "http://tools"
    os.environ["OPENAI_API_KEY"] = "test-key"

    from services.agent.app.tool_client import ToolClient

    ToolClient.set_default_transport(httpx.ASGITransport(app=tools_app))

    # Patch ChatOpenAI to a test stub (offline/deterministic).
    from tests.llm_stub import ChatOpenAIStub
    import services.agent.app.model as model

    model.ChatOpenAI = ChatOpenAIStub  # type: ignore[misc]
    from services.agent.app.main import app

    return app


@pytest.mark.asyncio
async def test_golden_sessions(agent_app):
    sessions = json.loads((REPO_ROOT / "tests" / "replay" / "golden_sessions.json").read_text(encoding="utf-8"))
    transport = httpx.ASGITransport(app=agent_app)

    async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
        for s in sessions:
            session_id = None
            last_offer_id = None
            last_prices = []
            tool_timeline = []
            last_agent_state = None
            last_assistant = None

            for t in s["turns"]:
                msg = t["message"]
                msg = _resolve_macros(msg)
                if msg == "__SELECT_FIRST_OFFER__":
                    assert last_offer_id is not None, (
                        f"golden session '{s['name']}' had no offers to select; "
                        f"last_agent_state={last_agent_state} last_assistant={last_assistant!r}"
                    )
                    msg = f"I choose {last_offer_id}"

                r = await client.post("/chat", json={"session_id": session_id, "user_id": "replay", "message": msg})
                assert r.status_code == 200
                data = r.json()
                session_id = data["session_id"]
                last_agent_state = data.get("agent_state")
                last_assistant = data.get("assistant_message")
                tool_timeline = data.get("tool_timeline") or []
                recs = data.get("recommended_offers") or []
                if recs:
                    last_offer_id = str(recs[0]["offer_id"])
                    last_prices = [float(o["total_price"]) for o in recs[:3]]

                # Invariants
                if tool_timeline:
                    assert check_tool_order(tool_timeline) == []
                assert check_grounding_no_invented_prices(data["assistant_message"], last_prices) == []

            # No repricing step in the workflow; selection confirms from already-fetched tool data.

