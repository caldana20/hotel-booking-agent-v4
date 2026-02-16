FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY README.md /app/README.md
COPY services/tools /app/services/tools
COPY db /app/db

RUN pip install --no-cache-dir -e ".[dev]"

EXPOSE 8001

CMD ["uvicorn", "services.tools.app.main:app", "--host", "0.0.0.0", "--port", "8001"]

