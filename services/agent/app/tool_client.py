from __future__ import annotations

import json
import time
from typing import Any

import httpx
from opentelemetry import trace

from services.agent.app.logging import logger
from services.agent.app.observability import TOOL_ERROR_TOTAL, TOOL_LATENCY
from services.agent.app.settings import SETTINGS


class ToolClientError(RuntimeError):
    pass


class ToolClient:
    _default_transport: httpx.AsyncBaseTransport | None = None

    @classmethod
    def set_default_transport(cls, transport: httpx.AsyncBaseTransport | None) -> None:
        cls._default_transport = transport

    def __init__(self, base_url: str):
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(SETTINGS.tool_timeout_ms / 1000.0)
        self._transport = self._default_transport

    async def _post_json(self, url: str, payload: dict[str, Any]) -> tuple[httpx.Response, int]:
        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            # When transport is set (tests), this routes to an in-process ASGI app.
            resp = await client.post(url, json=payload)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return resp, latency_ms

    def _ok_event(
        self,
        *,
        tool_name: str,
        path: str,
        url: str,
        payload: dict[str, Any],
        data: Any,  # noqa: ANN401 - tool responses are dynamic JSON
        latency_ms: int,
        retries: int,
    ) -> dict[str, Any]:
        return {
            "tool_name": tool_name,
            "path": path,
            "url": url,
            "status": "OK",
            "latency_ms": latency_ms,
            "retries": retries,
            "result_counts": _result_counts(tool_name, data if isinstance(data, dict) else {}),
            # Debug-only fields: keep snapshots useful without dumping huge payloads/results.
            "payload": _truncate_json(payload),
            "response_keys": sorted([str(k) for k in (data.keys() if isinstance(data, dict) else [])]),
            "result_preview": _result_preview(tool_name, data if isinstance(data, dict) else {}),
        }

    async def call(self, tool_name: str, path: str, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """
        Returns: (result_json, timeline_event)
        """
        tracer = trace.get_tracer("agent.tools")
        url = f"{self._base_url}{path}"

        retries = 0
        last_exc: Exception | None = None
        start = time.perf_counter()

        with tracer.start_as_current_span("tool_call") as span:
            span.set_attribute("tool.name", tool_name)
            for attempt in range(SETTINGS.tool_max_retries + 1):
                try:
                    logger.info("tool_call_started", tool_name=tool_name, attempt=attempt)
                    resp, latency_ms = await self._post_json(url, payload)
                    TOOL_LATENCY.labels(tool_name).observe(latency_ms)
                    span.set_attribute("tool.latency_ms", latency_ms)
                    span.set_attribute("http.status_code", resp.status_code)

                    if resp.status_code >= 400:
                        TOOL_ERROR_TOTAL.labels(tool_name).inc()
                        raise ToolClientError(f"tool {tool_name} failed: {resp.status_code} {resp.text}")

                    data = resp.json()
                    elapsed_ms = int((time.perf_counter() - start) * 1000)
                    event = self._ok_event(
                        tool_name=tool_name,
                        path=path,
                        url=url,
                        payload=payload,
                        data=data,
                        latency_ms=latency_ms,
                        retries=retries,
                    )
                    logger.info(
                        "tool_call_finished",
                        tool_name=tool_name,
                        status="OK",
                        latency_ms=latency_ms,
                        elapsed_ms=elapsed_ms,
                        result_counts=event["result_counts"],
                    )
                    return data, event
                except Exception as e:  # noqa: BLE001
                    last_exc = e
                    if attempt < SETTINGS.tool_max_retries:
                        retries += 1
                        continue
                    latency_ms = int((time.perf_counter() - start) * 1000)
                    TOOL_ERROR_TOTAL.labels(tool_name).inc()
                    span.record_exception(e)
                    logger.info(
                        "tool_call_finished",
                        tool_name=tool_name,
                        status="ERROR",
                        latency_ms=latency_ms,
                    )
                    raise

        raise ToolClientError(f"tool {tool_name} failed: {last_exc}")


def _result_counts(tool_name: str, data: dict[str, Any]) -> dict[str, int] | None:
    if tool_name == "search_candidates":
        cands = data.get("candidates") or []
        return {"candidates": len(cands)}
    if tool_name == "get_offers":
        offers = data.get("offers") or []
        return {"offers": len(offers)}
    if tool_name == "rank_offers":
        ranked = data.get("ranked_offers") or []
        return {"ranked_offers": len(ranked)}
    return None


def _truncate_json(obj: Any, *, max_str: int = 4000, max_list: int = 50, max_depth: int = 6) -> Any:  # noqa: ANN401
    """
    Prevent session snapshots from exploding in size while still being helpful in the UI debug panel.
    This is not security redaction; it's purely size-bounding for developer ergonomics.
    """

    def rec(x: Any, depth: int) -> Any:  # noqa: ANN401
        if depth <= 0:
            return "__truncated_depth__"
        if x is None or isinstance(x, (bool, int, float)):
            return x
        if isinstance(x, str):
            return x if len(x) <= max_str else (x[: max_str - 20] + "...__truncated__")
        if isinstance(x, list):
            if len(x) > max_list:
                return [rec(v, depth - 1) for v in x[:max_list]] + [f"__truncated_list_len={len(x)}__"]
            return [rec(v, depth - 1) for v in x]
        if isinstance(x, dict):
            out: dict[str, Any] = {}
            for k, v in x.items():
                out[str(k)] = rec(v, depth - 1)
            return out
        # Fallback: try JSON, else string
        try:
            json.dumps(x, default=str)
            return str(x)
        except Exception:  # noqa: BLE001
            return "__unserializable__"

    return rec(obj, max_depth)


def _result_preview(tool_name: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """
    Small, consistent preview for the UI debug panel.
    """
    try:
        if tool_name == "search_candidates":
            cands = data.get("candidates") or []
            top = []
            for c in cands[:3]:
                top.append(
                    {
                        "hotel_id": c.get("hotel_id"),
                        "name": c.get("name"),
                        "city": c.get("city"),
                        "neighborhood": c.get("neighborhood"),
                        "star_rating": c.get("star_rating"),
                        "review_score": c.get("review_score"),
                    }
                )
            return {"candidates_top": top, "counts": data.get("counts")}
        if tool_name == "get_offers":
            offers = data.get("offers") or []
            top = []
            for o in offers[:3]:
                top.append(
                    {
                        "offer_id": o.get("offer_id"),
                        "hotel_id": o.get("hotel_id"),
                        "total_price": o.get("total_price"),
                        "inventory_status": o.get("inventory_status"),
                        "expires_ts": o.get("expires_ts"),
                    }
                )
            return {"offers_top": top}
        if tool_name == "rank_offers":
            ranked = data.get("ranked_offers") or []
            top = []
            for r in ranked[:3]:
                off = (r.get("offer") or {}) if isinstance(r, dict) else {}
                top.append({"offer_id": off.get("offer_id"), "score": r.get("score")})
            return {"ranked_top": top}
    except Exception:  # noqa: BLE001
        return None
    return None

