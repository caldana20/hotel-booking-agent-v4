from __future__ import annotations

import os

import httpx
import pytest


@pytest.fixture()
def agent_app(migrated_seeded_db: str):
    os.environ["DATABASE_URL"] = migrated_seeded_db
    os.environ["DEFAULT_TENANT_ID"] = "t_default"
    os.environ["TOOLS_BASE_URL"] = "http://tools"
    os.environ["OPENAI_API_KEY"] = "test-key"

    # No tools needed for this contract test; ensure tool client won't be used.
    # Patch ChatOpenAI to a test stub (offline/deterministic).
    from tests.llm_stub import ChatOpenAIStub
    import services.agent.app.model as model

    model.ChatOpenAI = ChatOpenAIStub  # type: ignore[misc]
    from services.agent.app.main import app

    return app


@pytest.mark.asyncio
async def test_chat_rejects_extra_fields(agent_app):
    transport = httpx.ASGITransport(app=agent_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
        r = await client.post(
            "/chat",
            json={"session_id": None, "user_id": "u1", "message": "hello", "extra": "nope"},
        )
        assert r.status_code == 422

