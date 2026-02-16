from __future__ import annotations

import time
from collections.abc import Callable

from fastapi import FastAPI, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    GCCollector,
    Histogram,
    PlatformCollector,
    ProcessCollector,
    generate_latest,
)

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


REGISTRY = CollectorRegistry()
ProcessCollector(registry=REGISTRY)
PlatformCollector(registry=REGISTRY)
GCCollector(registry=REGISTRY)

REQUEST_SUCCESS_TOTAL = Counter(
    "request_success_total",
    "Count of successful requests",
    ["service", "route", "method"],
    registry=REGISTRY,
)
REQUEST_LATENCY = Histogram(
    "request_latency_ms",
    "Request latency in milliseconds",
    ["service", "route", "method"],
    buckets=(5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000),
    registry=REGISTRY,
)

TOOL_LATENCY = Histogram(
    "tool_latency_ms",
    "Tool call latency in milliseconds",
    ["tool"],
    buckets=(5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000),
    registry=REGISTRY,
)
TOOL_ERROR_TOTAL = Counter("tool_error_total", "Tool call errors", ["tool"], registry=REGISTRY)
FALLBACK_TOTAL = Counter("fallback_total", "Fallbacks triggered", ["kind"], registry=REGISTRY)


def setup_tracing(app: FastAPI, service_name: str) -> None:
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()


def instrument_sqlalchemy(engine) -> None:
    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)


def add_metrics_middleware(app: FastAPI, service_name: str) -> None:
    @app.middleware("http")
    async def _metrics(request: Request, call_next: Callable) -> Response:
        start = time.perf_counter()
        resp = await call_next(request)
        route = request.scope.get("path", "unknown")
        method = request.method
        REQUEST_LATENCY.labels(service_name, route, method).observe((time.perf_counter() - start) * 1000)
        if resp.status_code < 500:
            REQUEST_SUCCESS_TOTAL.labels(service_name, route, method).inc()
        return resp

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)

