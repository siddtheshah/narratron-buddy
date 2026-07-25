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
COPY agent.py .
COPY combined_app.py .
COPY web_viewer_app.py .
COPY bidi_app.py .
COPY evaluate_narration.py .
COPY config.yaml .

# Copy package directories
COPY components/ components/
COPY deployer/__init__.py deployer/__init__.py
COPY deployer/database.py deployer/database.py
COPY deployer/deployer.py deployer/deployer.py
COPY deployer/session_manager.py deployer/session_manager.py
COPY services/ services/
COPY tools/ tools/
COPY utils/ utils/
COPY templates/ templates/
COPY static/ static/

# Create runtime directories (these will be ephemeral on Cloud Run;
# use a volume mount or GCS bucket if you need persistence)
RUN mkdir -p sessions output playlists reference_library

# Copy default playlist and reference library assets that are checked in
COPY playlists/ playlists/
COPY reference_library/ reference_library/

# Cloud Run injects the PORT env var (default 8080)
ENV PORT=8080

# Run as non-root for security
RUN groupadd -r appuser && useradd -r -g appuser appuser
RUN chown -R appuser:appuser /app
USER appuser

EXPOSE ${PORT}

# Start the app — Cloud Run requires listening on 0.0.0.0:$PORT
CMD ["sh", "-c", "uvicorn combined_app:app --host 0.0.0.0 --port ${PORT}"]
