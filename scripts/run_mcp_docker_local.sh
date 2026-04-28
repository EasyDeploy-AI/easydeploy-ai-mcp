#!/usr/bin/env bash
# Build the repo Dockerfile and run the HTTP MCP locally (same image as deploy).
#
# Usage:
#   ./scripts/run_mcp_docker_local.sh
#   ./scripts/run_mcp_docker_local.sh -e EDA_API_KEY="eda_live_…" -e EDA_API_BASE="https://…"
#   PORT=9000 ./scripts/run_mcp_docker_local.sh …
#
# If a file named `.env` exists in the repo root, it is passed as `--env-file` (gitignored).
# Create a `.env` file with your env vars (see README for the full list).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

IMAGE="${EDA_MCP_IMAGE:-easydeploy-ai-mcp:local}"
PORT="${PORT:-8080}"

echo "Building ${IMAGE} …"
docker build -t "${IMAGE}" .

RUN_ARGS=(--rm -p "${PORT}:8080")
if [[ -f .env ]]; then
  RUN_ARGS+=(--env-file "${ROOT}/.env")
  echo "Using ${ROOT}/.env"
else
  echo "Note: no ${ROOT}/.env — container starts with image defaults only." >&2
  echo "      Create a .env file with your env vars (see README) and rerun." >&2
fi

exec docker run "${RUN_ARGS[@]}" "$@" "${IMAGE}"
