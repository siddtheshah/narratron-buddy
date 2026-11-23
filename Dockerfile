# syntax=docker/dockerfile:1
FROM golang:1.26-bookworm AS cel-worker
WORKDIR /src
COPY game_engine/cel_go_worker/go.mod game_engine/cel_go_worker/go.sum ./
RUN go mod download
COPY game_engine/cel_go_worker/ ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /cel-go-worker .

FROM node:22-bookworm-slim AS frontend-build
WORKDIR /app
COPY package.json package-lock.json tsconfig.json tsconfig.build.json ./
RUN npm ci
COPY scripts/build_canvas.mjs scripts/build_canvas.mjs
COPY frontend/ frontend/
RUN npm run build

FROM python:3.13-slim
COPY --from=cel-worker /cel-go-worker /usr/local/bin/cel-go-worker
ENV NARRATRON_CEL_BACKEND=go \
    NARRATRON_CEL_GO_WORKER=/usr/local/bin/cel-go-worker \
    NARRATRON_CEL_GO_POOL_SIZE=2

# Install uv from official distroless image
COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /uvx /bin/

# Install system dependencies (curl for healthchecks, ca-certificates for TLS)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10001 appuser \
    && useradd --system --uid 10001 --gid appuser --create-home appuser \
    && mkdir -p /mnt/storage/adventures /mnt/storage/games /mnt/storage/avatars /mnt/storage/stamps /mnt/storage/theaters /mnt/storage/music_catalog /tmp/ephemeral \
    && chown -R appuser:appuser /mnt/storage /tmp/ephemeral

WORKDIR /app

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy dependency definition files first for caching
COPY pyproject.toml uv.lock ./

# Install dependencies into a persistent virtual environment
ENV UV_PROJECT_ENVIRONMENT="/opt/venv"
ENV PLAYWRIGHT_BROWSERS_PATH="/opt/playwright"
RUN uv sync --frozen --no-dev --no-install-project \
    && uv run --no-sync python -m playwright install --with-deps --no-shell chromium \
    && uv run --no-sync python -c "from google.adk.sessions import DatabaseSessionService; DatabaseSessionService('sqlite+aiosqlite:///:memory:')" \
    && chown -R appuser:appuser /opt/venv

# Place virtualenv executables at the front of PATH
ENV PATH="/opt/venv/bin:$PATH"

# Copy application source
COPY --chown=appuser:appuser main.py .
COPY --chown=appuser:appuser object_registry.py .
COPY --chown=appuser:appuser ABOUT.md LICENSE ./
COPY --chown=appuser:appuser app.yaml .
COPY --chown=appuser:appuser theater_default.yaml .
COPY --chown=appuser:appuser api_server/ api_server/
COPY --chown=appuser:appuser components/ components/
COPY --chown=appuser:appuser game_engine/ game_engine/
COPY --chown=appuser:appuser services/ services/
COPY --chown=appuser:appuser storage/ storage/
COPY --chown=appuser:appuser testlab/ testlab/
COPY --chown=appuser:appuser tools/ tools/
COPY --chown=appuser:appuser utils/ utils/
COPY --chown=appuser:appuser frontend/templates/ frontend/templates/
COPY --chown=appuser:appuser frontend/static/ frontend/static/
COPY --chown=appuser:appuser --from=frontend-build /app/frontend/static/generated/ frontend/static/generated/
COPY --chown=appuser:appuser pricing/ pricing/
COPY --chown=appuser:appuser providers/ providers/
COPY --chown=appuser:appuser models/ models/
COPY --chown=appuser:appuser site_docs/ site_docs/
COPY --chown=appuser:appuser plugins/narratron-theater/ plugins/narratron-theater/

# Copy default theater assets that are checked in
COPY --chown=appuser:appuser example_theater/ example_theater/

RUN chown appuser:appuser /app

USER appuser

EXPOSE 8080

# Start the app — Cloud Run requires listening on 0.0.0.0:$PORT
CMD ["sh", "-c", "exec /opt/venv/bin/python main.py --host=0.0.0.0 --port=${PORT:-8080}"]
