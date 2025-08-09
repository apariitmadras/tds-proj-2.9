# Use a slim Python base image
FROM python:3.11-slim

# Set work directory
WORKDIR /app

# Install system dependencies (needed for Playwright and other packages)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# ✅ Install Playwright Chromium and dependencies
RUN python -m playwright install --with-deps chromium

# Copy the rest of the project files
COPY . .
