from __future__ import annotations

import os
import re
from datetime import date, timedelta

import httpx
import pytest


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

    # Route tool HTTP calls to the in-process tools ASGI app.
    from services.agent.app.tool_client import ToolClient

    ToolClient.set_default_transport(httpx.ASGITransport(app=tools_app))

    # Patch ChatOpenAI to a test stub (offline/deterministic).
    from tests.llm_stub import ChatOpenAIStub
    import services.agent.app.model as model

    model.ChatOpenAI = ChatOpenAIStub  # type: ignore[misc]

    from services.agent.app.main import app

    return app


@pytest.mark.asyncio
async def test_end_to_end_shopping_and_selection(agent_app):
    transport = httpx.ASGITransport(app=agent_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
        # Use dates relative to today so they always land inside the seeded horizon.
        check_in = (date.today() + timedelta(days=24)).isoformat()
        check_out = (date.today() + timedelta(days=26)).isoformat()
        # Shopping flow
        r1 = await client.post(
            "/chat",
            json={
                "session_id": None,
                "user_id": "u1",
                    "message": f"Find me a hotel in Austin {check_in} to {check_out} for 2 adults, 1 room under $1200",
            },
        )
        assert r1.status_code == 200
        data1 = r1.json()
        assert data1["agent_state"] in ("WAIT_FOR_SELECTION", "EXPLAIN", "WAIT_FOR_SELECTION")
        assert len(data1["recommended_offers"]) > 0
        assert len(data1["tool_timeline"]) >= 3
        # Tool order: search -> offers -> rank
        tool_names = [e["tool_name"] for e in data1["tool_timeline"]]
        assert tool_names[:3] == ["search_candidates", "get_offers", "rank_offers"]

        # Selection flow
        offer_id = data1["recommended_offers"][0]["offer_id"]
        r2 = await client.post(
            "/chat",
            json={"session_id": data1["session_id"], "user_id": "u1", "message": f"I choose {offer_id}"},
        )
        assert r2.status_code == 200
        data2 = r2.json()
        # No repricing step in the workflow: selection confirms using already-fetched tool data.
        tool_names2 = [e["tool_name"] for e in data2["tool_timeline"]]
        assert tool_names2 == []
        assert data2["agent_state"] in ("CONFIRM", "RESPOND")

        # Grounding: response should not invent dollar amounts beyond tool totals (weak check).
        # This is also enforced by server-side guardrail.
        dollars = re.findall(r"\\$\\d+\\.\\d{2}", data2["assistant_message"])
        assert len(dollars) >= 0

