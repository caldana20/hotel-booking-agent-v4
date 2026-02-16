# hotel-booking-agent-v4

End-to-end MVP: Hotel Shopping AI Agent (LangGraph + FastAPI + Postgres) with typed tool endpoints, deterministic seeded internal DB, observability (OTEL + Prometheus + JSON logs), tests, evals, and a Next.js webchat UI with one-click Jaeger trace links.

## Quickstart (Local)

Prereqs:
- Docker Desktop
- Python 3.11+
- Node 20+ (optional if using Docker for web)

Start the stack:
```bash
docker compose -f infra/docker-compose.yml up --build
```

Apply migrations:
```bash
alembic -c db/migrations/alembic.ini upgrade head
```

Seed the DB:
```bash
python -m db.seed
```

Run tests:
```bash
pytest
```

Run evals:
```bash
python -m evals.run --golden tests/replay/golden_sessions.json --cases evals/cases.json --out evals/out.json
```

Local URLs:
- Webchat UI: http://localhost:3000
- Agent API: http://localhost:8000
- Tools API: http://localhost:8001
- Jaeger: http://localhost:16686

## Curl

Agent chat:
```bash
curl -s localhost:8000/chat \
  -X POST -H 'content-type: application/json' \
  -d '{"session_id":null,"user_id":"u1","message":"Find me a hotel in Austin Mar 10-12 for 2 adults, budget under 250"}'
```

Tools:
```bash
curl -s localhost:8001/tools/search_candidates \
  -X POST -H 'content-type: application/json' \
  -d '{"tenant_id":"t_default","location":{"city":"Austin"},"check_in":"2026-03-10","check_out":"2026-03-12","occupancy":{"adults":2,"children":0,"rooms":1},"hard_filters":{"max_price":250}}'
```