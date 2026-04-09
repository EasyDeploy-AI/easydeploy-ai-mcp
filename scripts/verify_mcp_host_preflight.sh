#!/usr/bin/env bash
# Preflight: EDA_API_BASE responds on /v1/account; optional MCP base issuer matches pool.
# Usage: ./scripts/verify_mcp_host_preflight.sh
# Env: EDA_API_BASE (default: sandbox URL from .env.example)
#      MCP_BASE (optional HTTPS URL of MCP host for GET /.well-known/oauth-protected-resource)
#      EXPECTED_ISSUER (optional; default derived from pool id below)
#      EDA_COGNITO_USER_POOL_ID (optional; default us-east-1_WLnwphMyA)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

EDA_API_BASE="${EDA_API_BASE:-https://h3h0z4vkf1.execute-api.us-east-1.amazonaws.com/prod}"
POOL="${EDA_COGNITO_USER_POOL_ID:-us-east-1_WLnwphMyA}"
REGION="${EDA_COGNITO_REGION:-us-east-1}"
EXPECTED_ISSUER="${EXPECTED_ISSUER:-https://cognito-idp.${REGION}.amazonaws.com/${POOL}}"

# Normalize: ensure we hit .../v1/account
BASE="${EDA_API_BASE%/}"
if [[ "$BASE" == */v1 ]]; then
  ACCT_URL="${BASE}/account"
else
  ACCT_URL="${BASE}/v1/account"
fi

echo "==> GET $ACCT_URL (expect 401 without bearer)"
code="$(curl -sS -o /dev/null -w '%{http_code}' "$ACCT_URL" || true)"
if [[ "$code" != "401" && "$code" != "403" ]]; then
  echo "FAIL: expected 401 or 403 without Authorization, got HTTP $code" >&2
  exit 1
fi
echo "OK: HTTP $code"

if [[ -n "${MCP_BASE:-}" ]]; then
  MCP="${MCP_BASE%/}"
  META="$MCP/.well-known/oauth-protected-resource"
  echo "==> GET $META"
  body="$(curl -sS "$META")"
  if ! echo "$body" | grep -q "authorization_servers"; then
    echo "FAIL: missing authorization_servers in response" >&2
    echo "$body" >&2
    exit 1
  fi
  if ! echo "$body" | grep -qF "$EXPECTED_ISSUER"; then
    echo "WARN: response does not contain expected issuer $EXPECTED_ISSUER" >&2
    echo "$body" >&2
    exit 1
  fi
  echo "OK: issuer matches expected pool issuer"
fi

echo "Preflight passed."
