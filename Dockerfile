# ── Stage 1: dependency layer ─────────────────────────────────────────────────
# This layer is cached as long as requirements.txt doesn't change.
# A code-only push skips this entire stage and goes straight to Stage 2.
FROM python:3.11-slim AS base

# uv: Rust-based pip replacement, 10-100x faster installs on cache misses
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install system deps needed by psycopg2-binary and lxml
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

# COPY requirements FIRST — Docker caches the pip install layer separately
# from your source code. Requirements only reinstall when this file changes.
COPY requirements.txt .

# Install into the system Python (no venv needed in Docker)
# uv resolves + installs everything in seconds even on a cold cache
RUN uv pip install --system --no-cache -r requirements.txt

# ── Stage 2: application layer ────────────────────────────────────────────────
# This layer rebuilds on every push — it's just copying files, takes ~2 seconds.
COPY . .

# Collect static files / run any one-time setup if needed
# (nothing needed here for Flask)

EXPOSE 10000

# Matches your existing Procfile command
CMD ["gunicorn", "app:app", "--workers", "2", "--bind", "0.0.0.0:10000", "--timeout", "120"]
