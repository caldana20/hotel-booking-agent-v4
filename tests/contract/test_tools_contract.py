from __future__ import annotations

import os
from datetime import date, timedelta

import httpx
import pytest


@pytest.fixture()
def tools_app(migrated_seeded_db: str):
    # Ensure env is set before importing settings-bound app.
    os.environ["DATABASE_URL"] = migrated_seeded_db
    os.environ["DEFAULT_TENANT_ID"] = "t_default"
    from services.tools.app.main import app

    return app


@pytest.mark.asyncio
async def test_search_candidates_rejects_extra_fields(tools_app):
    transport = httpx.ASGITransport(app=tools_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "tenant_id": "t_default",
            "location": {"city": "Austin"},
            "check_in": str(date.today() + timedelta(days=7)),
            "check_out": str(date.today() + timedelta(days=9)),
            "occupancy": {"adults": 2, "children": 0, "rooms": 1},
            "hard_filters": {"max_price": 250},
            "extra": "nope",
        }
        r = await client.post("/tools/search_candidates", json=payload)
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_search_candidates_rejects_invalid_dates(tools_app):
    transport = httpx.ASGITransport(app=tools_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        d = date.today() + timedelta(days=7)
        payload = {
            "tenant_id": "t_default",
            "location": {"city": "Austin"},
            "check_in": str(d),
            "check_out": str(d),
            "occupancy": {"adults": 2, "children": 0, "rooms": 1},
        }
        r = await client.post("/tools/search_candidates", json=payload)
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_tenant_isolation_rejects_wrong_tenant(tools_app):
    transport = httpx.ASGITransport(app=tools_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "tenant_id": "t_other",
            "location": {"city": "Austin"},
            "check_in": str(date.today() + timedelta(days=7)),
            "check_out": str(date.today() + timedelta(days=9)),
            "occupancy": {"adults": 2, "children": 0, "rooms": 1},
        }
        r = await client.post("/tools/search_candidates", json=payload)
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_get_offers_rejects_extra_fields(tools_app):
    transport = httpx.ASGITransport(app=tools_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "tenant_id": "t_default",
            "hotel_ids": ["00000000-0000-0000-0000-000000000000"],
            "trip": {
                "check_in": "2026-03-10",
                "check_out": "2026-03-12",
                "occupancy": {"adults": 2, "children": 0, "rooms": 1},
            },
            "currency": "USD",
            "extra": 1,
        }
        r = await client.post("/tools/get_offers", json=payload)
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_rank_offers_rejects_extra_fields(tools_app):
    transport = httpx.ASGITransport(app=tools_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {"offers": [], "extra": "nope"}
        r = await client.post("/tools/rank_offers", json=payload)
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_search_candidates_city_normalizes_state_suffix(tools_app):
    """
    Regression: the agent/LLM may send "Seattle, WA" while the DB stores city as "Seattle".
    Tools should accept both.
    """
    transport = httpx.ASGITransport(app=tools_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        check_in = date.today() + timedelta(days=24)
        check_out = date.today() + timedelta(days=27)
        payload_base = {
            "tenant_id": "t_default",
            "location": {"city": "Seattle"},
            "check_in": str(check_in),
            "check_out": str(check_out),
            "occupancy": {"adults": 2, "children": 0, "rooms": 1},
        }
        payload_suffix = dict(payload_base)
        payload_suffix["location"] = {"city": "Seattle, WA"}

        r1 = await client.post("/tools/search_candidates", json=payload_base)
        r2 = await client.post("/tools/search_candidates", json=payload_suffix)
        assert r1.status_code == 200
        assert r2.status_code == 200
        d1 = r1.json()
        d2 = r2.json()

        # The goal is normalization parity (not guaranteeing Seattle has candidates on every date).
        ids1 = sorted([c.get("hotel_id") for c in (d1.get("candidates") or [])])
        ids2 = sorted([c.get("hotel_id") for c in (d2.get("candidates") or [])])
        assert ids1 == ids2
        assert (d1.get("counts") or {}).get("candidates") == (d2.get("counts") or {}).get("candidates")

