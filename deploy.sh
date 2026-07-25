#!/usr/bin/env bash
# deploy.sh — Build & deploy narratron-buddy to Google Cloud Run
#
# Prerequisites:
#   1. gcloud CLI installed and authenticated (`gcloud auth login`)
#   2. Docker or gcloud configured for building images
#   3. Required env vars set in Cloud Run (see below)
#
# Usage:
#   ./deploy.sh                   # deploy to default project/region
#   ./deploy.sh --tag v1.2.3      # deploy a specific tag

set -euo pipefail

# ---- Load .env file if present ----
ENV_FILE="${ENV_FILE:-.env}"
if [[ -f "$ENV_FILE" ]]; then
  echo "==> Loading env vars from ${ENV_FILE}"
  set -a  # auto-export all sourced vars
  # shellcheck disable=SC1090
  source <(grep -v '^\s*#' "$ENV_FILE" | grep -v '^\s*$')
  set +a
else
  echo "WARNING: No ${ENV_FILE} found — relying on existing env vars"
fi

# ---- Configuration (edit these or override via env) ----
PROJECT_ID="${GCP_PROJECT_ID:-narratron}"
REGION="${GCP_REGION:-us-central1}"
SERVICE_NAME="${CLOUD_RUN_SERVICE:-narratron-buddy}"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

# Parse optional --tag argument
TAG="latest"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag) TAG="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# Verify required env vars are set
REQUIRED_VARS=(
  GOOGLE_GENAI_USE_VERTEXAI
  GEMINI_API_KEY
  GOOGLE_CLOUD_PROJECT
  GOOGLE_CLOUD_LOCATION
  SMTP_USERNAME
  SMTP_PASSWORD
  TURSO_DATABASE_URL
  TURSO_DB_TOKEN
)
for var in "${REQUIRED_VARS[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    echo "ERROR: Required env var ${var} is not set (check your .env file)"
    exit 1
  fi
done

FULL_IMAGE="${IMAGE}:${TAG}"

echo "==> Building image: ${FULL_IMAGE}"
gcloud builds submit --tag "${FULL_IMAGE}" --project "${PROJECT_ID}"

echo "==> Deploying to Cloud Run: ${SERVICE_NAME} in ${REGION}"
gcloud run deploy "${SERVICE_NAME}" \
  --image "${FULL_IMAGE}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --platform managed \
  --allow-unauthenticated \
  --port 8080 \
  --memory 1Gi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 3 \
  --timeout 300 \
  --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=${GOOGLE_GENAI_USE_VERTEXAI}" \
  --set-env-vars "GEMINI_API_KEY=${GEMINI_API_KEY}" \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=${GOOGLE_CLOUD_PROJECT}" \
  --set-env-vars "GOOGLE_CLOUD_LOCATION=${GOOGLE_CLOUD_LOCATION}" \
  --set-env-vars "SMTP_USERNAME=${SMTP_USERNAME}" \
  --set-env-vars "SMTP_PASSWORD=${SMTP_PASSWORD}" \
  --set-env-vars "TURSO_DATABASE_URL=${TURSO_DATABASE_URL}" \
  --set-env-vars "TURSO_DB_TOKEN=${TURSO_DB_TOKEN}"

echo ""
echo "==> Deployed! Service URL:"
gcloud run services describe "${SERVICE_NAME}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --format "value(status.url)"
