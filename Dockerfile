# ---- Build stage: install dependencies ----
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build-essential for any native extensions
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- Runtime stage ----
FROM python:3.12-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY object_registry.py .
COPY main.py .
COPY evaluate_narration.py .
COPY app.yaml .
COPY theater_default.yaml .
COPY ABOUT.md .

# Copy package directories
COPY components/ components/
COPY api_server/ api_server/
COPY deployer/ deployer/
COPY storage/ storage/
COPY services/ services/
COPY tools/ tools/
COPY utils/ utils/
COPY templates/ templates/
COPY static/ static/
COPY pricing/ pricing/

# Cloud Run theater data is intentionally ephemeral and stored under /tmp.
RUN mkdir -p /tmp/theaters output playlists reference_library

# Copy default playlist and reference library assets that are checked in
COPY playlists/ playlists/
COPY reference_library/ reference_library/

# Run as non-root for security
RUN groupadd -r appuser && useradd -r -g appuser appuser
RUN chown -R appuser:appuser /app /tmp/theaters
USER appuser

EXPOSE 8080

# Start the app — Cloud Run requires listening on 0.0.0.0:$PORT
CMD ["sh", "-c", "python main.py --use_cloud_theater_storage --host=0.0.0.0 --port=8080"]
