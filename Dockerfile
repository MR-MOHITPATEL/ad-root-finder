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
EXPOSE 7860

# Hugging Face Spaces expects the app on 7860. 1 worker (shared job state + SQLite fallback).
CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "1"]
