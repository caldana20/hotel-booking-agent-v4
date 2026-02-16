FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY README.md /app/README.md
COPY services/agent /app/services/agent
COPY services/tools /app/services/tools
COPY db /app/db
COPY evals /app/evals

RUN pip install --no-cache-dir -e ".[dev]"

EXPOSE 8000

CMD ["uvicorn", "services.agent.app.main:app", "--host", "0.0.0.0", "--port", "8000"]

