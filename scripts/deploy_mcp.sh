#!/usr/bin/env bash
#
# Renders tools.yaml for the active environment, publishes it to Secret Manager
# and (re)deploys the Database Toolbox MCP microservice on Cloud Run.
#
# Every value is taken from the environment (.env is sourced when present), so
# the same script deploys dev / staging / prod without editing any source file.
#
# Usage:
#   ./scripts/deploy_mcp.sh              # render + secret + deploy
#   ./scripts/deploy_mcp.sh --render     # render only (build/tools.rendered.yaml)
#   ./scripts/deploy_mcp.sh --verify     # verify the live MCP tool contract
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

PROJECT_ID="${PROJECT_ID:-${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null)}}"
REGION="${REGION:-us-central1}"
BIGTABLE_INSTANCE_ID="${BIGTABLE_INSTANCE_ID:-operations-db}"
BIGTABLE_TABLE_ID="${BIGTABLE_TABLE_ID:-cashier_realtime_alerts}"
MCP_SERVICE_NAME="${MCP_SERVICE_NAME:-mcp-toolbox-bigtable}"
MCP_SECRET_NAME="${MCP_SECRET_NAME:-bigtable-mcp-tools-secret}"
TOOLBOX_IMAGE="${TOOLBOX_IMAGE:-us-central1-docker.pkg.dev/database-toolbox/toolbox/toolbox:latest}"
RENDERED="build/tools.rendered.yaml"

if [[ -z "${PROJECT_ID}" ]]; then
  echo "ERROR: PROJECT_ID is not set and gcloud has no default project." >&2
  exit 1
fi

export PROJECT_ID REGION BIGTABLE_INSTANCE_ID BIGTABLE_TABLE_ID

render() {
  mkdir -p build
  envsubst '${PROJECT_ID} ${BIGTABLE_INSTANCE_ID} ${BIGTABLE_TABLE_ID}' \
    < tools.yaml > "${RENDERED}"
  echo "Rendered ${RENDERED} for project=${PROJECT_ID} instance=${BIGTABLE_INSTANCE_ID}"
}

publish_secret() {
  if ! gcloud secrets describe "${MCP_SECRET_NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud secrets create "${MCP_SECRET_NAME}" --project="${PROJECT_ID}" --replication-policy=automatic
  fi
  gcloud secrets versions add "${MCP_SECRET_NAME}" \
    --project="${PROJECT_ID}" --data-file="${RENDERED}"
}

deploy() {
  gcloud run deploy "${MCP_SERVICE_NAME}" \
    --project="${PROJECT_ID}" \
    --region="${REGION}" \
    --image="${TOOLBOX_IMAGE}" \
    --no-allow-unauthenticated \
    --set-secrets="/app/tools.yaml=${MCP_SECRET_NAME}:latest" \
    --args="--tools-file=/app/tools.yaml,--address=0.0.0.0,--port=8080" \
    --port=8080 \
    --update-env-vars="CONFIG_REVISION=$(date +%s)"

  local url
  url="$(gcloud run services describe "${MCP_SERVICE_NAME}" \
    --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.url)')"
  echo ""
  echo "MCP microservice deployed. Add this to your .env:"
  echo "  BIGTABLE_MCP_URL=${url}"
}

verify() {
  local url token
  url="${BIGTABLE_MCP_URL:-$(gcloud run services describe "${MCP_SERVICE_NAME}" \
    --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.url)')}"
  token="$(gcloud auth print-identity-token)"
  echo "Verifying MCP tool contract at ${url}/mcp ..."
  curl -sS -m 30 -X POST "${url%/}/mcp" \
    -H "Authorization: Bearer ${token}" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
  echo ""
}

case "${1:-all}" in
  --render) render ;;
  --verify) verify ;;
  all|"") render; publish_secret; deploy; verify ;;
  *) echo "Unknown option: $1" >&2; exit 2 ;;
esac
