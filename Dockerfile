# ---- Build stage: install dependencies with uv ----
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build-essential for any native extensions
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Install uv from official binary image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Sync dependencies into /opt/venv exactly as resolved in uv.lock
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---- Runtime stage ----
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application source
COPY object_registry.py .
COPY main.py .
COPY app.yaml .
COPY theater_default.yaml .
COPY ABOUT.md .

# Copy package directories
COPY components/ components/
COPY api_server/ api_server/
COPY storage/ storage/
COPY services/ services/
COPY tools/ tools/
COPY utils/ utils/
COPY templates/ templates/
COPY static/ static/
COPY pricing/ pricing/
COPY providers/ providers/
COPY models/ models/
COPY docs/ docs/

# Copy default playlist and reference library assets that are checked in
COPY playlists/ playlists/
COPY reference_library/ reference_library/

EXPOSE 8080

# Start the app — Cloud Run requires listening on 0.0.0.0:$PORT
CMD ["sh", "-c", "python main.py --host=0.0.0.0 --port=${PORT:-8080}"]
