# Dedicated Dockerfile for Narratron Storage Daemon
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Runtime stage
FROM python:3.12-slim

WORKDIR /app

# Copy installed dependencies
COPY --from=builder /install /usr/local

# Copy application source code needed by DatabaseManager & LocalDeployer
COPY config.yaml .
COPY deployer/ deployer/
COPY utils/ utils/
COPY storage/ storage/

# Default directories for theaters data if using local storage
RUN mkdir -p /tmp/theaters

# Run as non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser
RUN chown -R appuser:appuser /app /tmp/theaters
USER appuser

# Environment default parameters
ENV STORAGE_INTERVAL_SECONDS=60
ENV STORAGE_TTL_SECONDS=604800
ENV STORAGE_HOURLY_COST=0.004167
ENV USE_LIVE_DB=false
ENV DB_PATH=deployer.db

# Run the storage daemon on persistent schedule
CMD ["python", "-m", "storage.storage_daemon"]
