# Dedicated Dockerfile for Narratron Storage Daemon
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

ADD https://astral.sh/uv/0.8.20/install.sh /tmp/uv-installer.sh
RUN UV_INSTALL_DIR=/bin UV_NO_MODIFY_PATH=1 sh /tmp/uv-installer.sh && \
    rm /tmp/uv-installer.sh
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Runtime stage
FROM python:3.12-slim

WORKDIR /app

# Copy installed dependencies
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /bin/uv /bin/uv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application source code needed by DatabaseManager & Theater Manager
COPY app.yaml .
COPY theater_default.yaml .
COPY components/ components/
COPY utils/ utils/
COPY storage/ storage/
COPY pricing/ pricing/

# Create the default theater storage directory before dropping privileges.
RUN mkdir -p /mnt/storage

# Run as non-root user
RUN groupadd -r appuser && useradd -r -m -g appuser appuser
RUN chown -R appuser:appuser /app /mnt/storage
USER appuser

# Environment default parameters
ENV STORAGE_INTERVAL_SECONDS=60
ENV STORAGE_TTL_SECONDS=604800
ENV STORAGE_HOURLY_COST=0.004167
ENV USE_LIVE_DB=false
ENV DB_PATH=deployer.db

# Run the storage daemon on persistent schedule
CMD ["uv", "run", "--no-project", "--python", "/opt/venv/bin/python", "-m", "storage.storage_daemon"]
