FROM python:3.13-slim AS base

RUN apt-get update && \
    apt-get install -y --no-install-recommends git bash && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# ---------- API (default) ----------
# Runs alembic migrations on startup, then serves the FastAPI app.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ---------- Worker (override CMD) ----------
# docker run eval-runner python -m hackathon_runner.worker --job-id <JOB_ID>
