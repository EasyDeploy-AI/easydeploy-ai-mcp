#!/usr/bin/env bash
# Smoke checks for sandbox MCP + public API (see docs/sandbox-mcp-validation.md).
set -euo pipefail

MCP_SMOKE_BASE_URL="${MCP_SMOKE_BASE_URL:-http://127.0.0.1:8080}"
EDA_API_BASE="${EDA_API_BASE:-https://h3h0z4vkf1.execute-api.us-east-1.amazonaws.com/prod}"

# Match normalize_api_base: strip trailing slashes, ensure single /v1 suffix
api_v1_base="${EDA_API_BASE%/}"
if [[ "${api_v1_base}" != */v1 ]]; then
  api_v1_base="${api_v1_base}/v1"
fi

# Direct API check: EDA_SMOKE_API_KEY or same name as MCP server (eda_live_…)
SMOKE_API_KEY="${EDA_SMOKE_API_KEY:-${EDA_API_KEY:-}}"
# MCP JWT steps: EDA_SMOKE_ACCESS_TOKEN or same as integration pytest
SMOKE_ACCESS_TOKEN="${EDA_SMOKE_ACCESS_TOKEN:-${EDA_INTEGRATION_COGNITO_ACCESS_TOKEN:-}}"

failures=0

echo "== 1) GET ${MCP_SMOKE_BASE_URL}/healthz"
if ! code=$(curl -sS -o /tmp/mcp_healthz.json -w "%{http_code}" "${MCP_SMOKE_BASE_URL}/healthz"); then
  echo "curl failed (is the MCP server running?)"
  exit 1
fi
if [[ "${code}" != "200" ]]; then
  echo "expected 200, got ${code}"
  failures=$((failures + 1))
else
  cat /tmp/mcp_healthz.json
  echo
fi

echo "== 2) GET ${MCP_SMOKE_BASE_URL}/.well-known/oauth-protected-resource"
if ! code=$(curl -sS -o /tmp/mcp_oauth_meta.json -w "%{http_code}" "${MCP_SMOKE_BASE_URL}/.well-known/oauth-protected-resource"); then
  echo "curl failed"
  failures=$((failures + 1))
elif [[ "${code}" == "404" ]]; then
  echo "404 (OAuth disabled — expected in legacy mode)"
elif [[ "${code}" != "200" ]]; then
  echo "expected 200 or 404, got ${code}"
  failures=$((failures + 1))
else
  cat /tmp/mcp_oauth_meta.json
  echo
fi

if [[ -n "${SMOKE_API_KEY}" ]]; then
  echo "== 3) GET ${api_v1_base}/account (Authorization: Bearer eda_live…)"
  if ! code=$(curl -sS -o /tmp/mcp_account.json -w "%{http_code}" \
    -H "Authorization: Bearer ${SMOKE_API_KEY}" \
    "${api_v1_base}/account"); then
    echo "curl failed"
    failures=$((failures + 1))
  elif [[ "${code}" != "200" ]]; then
    echo "expected 200, got ${code}"
    failures=$((failures + 1))
  else
    head -c 200 /tmp/mcp_account.json
    echo
  fi
else
  echo "== 3) SKIP (set EDA_API_KEY or EDA_SMOKE_API_KEY for direct API check)"
fi

MCP_ACCEPT="application/json, text/event-stream"

if [[ -n "${SMOKE_ACCESS_TOKEN}" ]]; then
  echo "== 4) POST ${MCP_SMOKE_BASE_URL}/mcp (initialize)"
  body='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}'
  if ! code=$(curl -sS -D /tmp/mcp_init.hdr -o /tmp/mcp_init.json -w "%{http_code}" \
    -H "Content-Type: application/json" \
    -H "Accept: ${MCP_ACCEPT}" \
    -H "Authorization: Bearer ${SMOKE_ACCESS_TOKEN}" \
    -d "${body}" \
    "${MCP_SMOKE_BASE_URL}/mcp"); then
    echo "curl failed"
    failures=$((failures + 1))
  elif [[ "${code}" == "401" ]]; then
    echo "401 — token rejected or MCP_SERVICE_TOKEN gate"
    failures=$((failures + 1))
  elif [[ "${code}" != "200" ]]; then
    echo "unexpected HTTP ${code}"
    failures=$((failures + 1))
  else
    head -c 400 /tmp/mcp_init.json
    echo
    session_id=""
    while IFS= read -r line || [[ -n "${line}" ]]; do
      line="${line//$'\r'/}"
      line_lc=$(printf '%s' "${line}" | tr '[:upper:]' '[:lower:]')
      if [[ "${line_lc}" == mcp-session-id:* ]]; then
        session_id="${line#*:}"
        session_id="${session_id#"${session_id%%[![:space:]]*}"}"
        break
      fi
    done < /tmp/mcp_init.hdr
    if [[ -z "${session_id}" ]]; then
      echo "missing mcp-session-id in initialize response"
      failures=$((failures + 1))
    else
      echo "== 5) POST ${MCP_SMOKE_BASE_URL}/mcp (tools/list, session ${session_id:0:8}…)"
      body_list='{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
      if ! code=$(curl -sS -o /tmp/mcp_tools_list.json -w "%{http_code}" \
        -H "Content-Type: application/json" \
        -H "Accept: ${MCP_ACCEPT}" \
        -H "Mcp-Session-Id: ${session_id}" \
        -H "Authorization: Bearer ${SMOKE_ACCESS_TOKEN}" \
        -d "${body_list}" \
        "${MCP_SMOKE_BASE_URL}/mcp"); then
        echo "curl failed"
        failures=$((failures + 1))
      elif [[ "${code}" == "401" ]]; then
        echo "401 on tools/list"
        failures=$((failures + 1))
      elif [[ "${code}" != "200" ]]; then
        echo "tools/list unexpected HTTP ${code}"
        failures=$((failures + 1))
      else
        head -c 500 /tmp/mcp_tools_list.json
        echo
      fi
    fi
  fi
else
  echo "== 4) SKIP (set EDA_SMOKE_ACCESS_TOKEN or EDA_INTEGRATION_COGNITO_ACCESS_TOKEN for OAuth /mcp check)"
fi

if [[ "${failures}" -gt 0 ]]; then
  echo "FAILED (${failures} check(s))"
  exit 1
fi
echo "OK"
