# ClaimVoice: one image serving the API, the live WebSocket, and the built React UI.

# --- 1. Build the frontend -----------------------------------------------------
FROM node:20-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- 2. Runtime ------------------------------------------------------------------
FROM python:3.14-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN useradd --create-home --uid 10001 claimvoice
WORKDIR /app

# Dependencies first so code changes don't reinstall them.
COPY backend/pyproject.toml backend/pyproject.toml
RUN mkdir -p backend/app && touch backend/app/__init__.py && pip install -e ./backend

COPY backend/ backend/
COPY --from=frontend /app/frontend/dist frontend/dist
RUN mkdir -p backend/data && chown -R claimvoice:claimvoice backend/data

USER claimvoice
EXPOSE 8000
# Claims (SQLite) and evidence photos live here; mount a volume to keep them.
VOLUME ["/app/backend/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"
# Migrations run automatically at startup (app lifespan).
CMD ["uvicorn", "app.api.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
