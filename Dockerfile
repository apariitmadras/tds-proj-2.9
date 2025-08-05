# Dockerfile (root) — minimal image with Playwright + Chromium
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

# System deps: CA certs; curl/git helpful for debug (kept minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

# Install Chromium + OS deps for Playwright
RUN python -m playwright install --with-deps chromium

# App code
COPY . /app

# Ensure outputs dir exists at runtime
RUN mkdir -p /app/outputs

EXPOSE 8000

# Start FastAPI
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
