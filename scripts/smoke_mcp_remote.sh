#!/usr/bin/env bash
# Run HTTP MCP smoke against a public (or staging) MCP URL with a bearer token.
# Obtain token via PKCE: scripts/cognito_mcp_get_access_token.py
#
# Required env:
#   MCP_SMOKE_BASE_URL   e.g. https://mcp.example.com  (no trailing slash)
#   EDA_SMOKE_ACCESS_TOKEN  Cognito access JWT or eda_live_…
#
# Optional: same vars as smoke-mcp-http.mjs (see scripts/mcp-streamable-client.mjs)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

: "${MCP_SMOKE_BASE_URL:?Set MCP_SMOKE_BASE_URL to https://your-host}"
: "${EDA_SMOKE_ACCESS_TOKEN:?Set EDA_SMOKE_ACCESS_TOKEN}"

export MCP_SMOKE_BASE_URL
export EDA_SMOKE_ACCESS_TOKEN

echo "==> smoke-mcp-http.mjs against $MCP_SMOKE_BASE_URL"
node scripts/smoke-mcp-http.mjs
