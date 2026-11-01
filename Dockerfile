# syntax=docker/dockerfile:1
FROM python:3.11-slim

# Install uv from official distroless image
COPY --from=ghcr.io/astral-sh/uv:0.6.5 /uv /uvx /bin/

# Install system dependencies (curl for healthchecks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy dependency definition files first for caching
COPY pyproject.toml uv.lock ./

# Install dependencies into a persistent virtual environment
ENV UV_PROJECT_ENVIRONMENT="/opt/venv"
RUN uv sync --frozen --no-dev --no-install-project

# Place virtualenv executables at the front of PATH
ENV PATH="/opt/venv/bin:$PATH"

# Copy application source
COPY main.py .
COPY app.yaml .
COPY theater_default.yaml .
COPY adventures/ adventures/
COPY api_server/ api_server/
COPY components/ components/
COPY game/ game/
COPY modules/ modules/
COPY services/ services/
COPY tools/ tools/
COPY utils/ utils/
COPY templates/ templates/
COPY static/ static/
COPY pricing/ pricing/
COPY providers/ providers/
COPY models/ models/
COPY docs/ docs/

# Copy default theater assets that are checked in
COPY example_theater/ example_theater/

EXPOSE 8080

# Start the app — Cloud Run requires listening on 0.0.0.0:$PORT
CMD ["sh", "-c", "exec uv run --no-project --python /opt/venv/bin/python main.py --host=0.0.0.0 --port=${PORT:-8080}"]
