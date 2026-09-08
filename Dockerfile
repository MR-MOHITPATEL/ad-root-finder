# Web UI only. The scraper runs on GitHub Actions, not here.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY rootfinder/ ./rootfinder/
COPY compliance/ ./compliance/
COPY config/ ./config/
COPY web/ ./web/
COPY run.py .

ENV PYTHONUNBUFFERED=1

# Railway injects $PORT at runtime; fall back to 8000 for local `docker run`.
# Shell form (not exec/JSON) so ${PORT} is expanded at container start.
# 1 worker — the in-process job state and SQLite fallback assume a single process.
CMD uvicorn web.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1
