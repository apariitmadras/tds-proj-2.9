# --- Dockerfile ---
FROM python:3.11-slim

WORKDIR /app

# System deps (keep minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browser + libs (needed by tools/scrape_website.py)
RUN python -m playwright install --with-deps chromium

# Copy the rest of the app
COPY . .

# Use Railway's PORT when present; fall back to 8000 locally
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000} --log-level info"]
