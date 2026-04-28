#!/usr/bin/env bash
# Quick REST check: public API /v1/account with $EDA_API_KEY (no MCP required).
#
# Usage:
#   export EDA_API_KEY="eda_live_…"
#   # optional: export EDA_API_BASE="https://…execute-api…/prod"
#   ./scripts/validate_sandbox_api_key.sh
#
# See docs/sandbox-mcp-validation.md for EDA_API_BASE shape (client appends /v1).
set -euo pipefail

if [[ -z "${EDA_API_KEY:-}" ]]; then
  echo "error: set EDA_API_KEY (e.g. export EDA_API_KEY='eda_live_…')" >&2
  exit 1
fi

EDA_API_BASE="${EDA_API_BASE:-https://YOUR_API_ID.execute-api.us-east-1.amazonaws.com/prod}"
api_v1_base="${EDA_API_BASE%/}"
if [[ "${api_v1_base}" != */v1 ]]; then
  api_v1_base="${api_v1_base}/v1"
fi

url="${api_v1_base}/account"
echo "== GET ${url} (no auth — expect 401)"
code=$(curl -sS -o /dev/null -w "%{http_code}" "$url")
if [[ "${code}" == "401" ]] || [[ "${code}" == "403" ]]; then
  echo "ok (got ${code})"
else
  echo "unexpected HTTP ${code} (expected 401 or 403 without bearer)" >&2
  exit 1
fi

echo "== GET ${url} (Authorization: Bearer \$EDA_API_KEY — expect 200)"
if ! code=$(curl -sS -o /tmp/eda_account_smoke.json -w "%{http_code}" \
  -H "Authorization: Bearer ${EDA_API_KEY}" \
  "$url"); then
  echo "curl failed" >&2
  exit 1
fi
if [[ "${code}" != "200" ]]; then
  echo "expected 200, got ${code}" >&2
  head -c 500 /tmp/eda_account_smoke.json >&2 || true
  exit 1
fi
head -c 300 /tmp/eda_account_smoke.json
echo
echo "OK"
