"""
Optional end-to-end check with a **real** Cognito access token and live JWKS fetch.

Skipped unless all env vars below are set. Does not call the EasyDeploy REST API
unless you add a separate tools/call test.

Run (after ``pip install -e ".[dev]"`` — includes ``pyjwt[crypto]``)::

    export EDA_INTEGRATION_COGNITO_ACCESS_TOKEN="eyJ..."  # access JWT from MCP app client
    export EDA_COGNITO_USER_POOL_ID="us-east-1_xxxx"
    export EDA_COGNITO_CLIENT_ID="<McpClaudeOauthUserPoolClientId>"
    export EDA_COGNITO_REGION="us-east-1"

    pytest tests/test_cognito_jwt_integration.py -v

Obtain a token with ``scripts/cognito_mcp_get_access_token.py`` or Claude OAuth.
"""

from __future__ import annotations

import importlib
import os

import pytest
from starlette.testclient import TestClient

MCP_ACCEPT = "application/json, text/event-stream"

_INTEGRATION_TOKEN = os.environ.get("EDA_INTEGRATION_COGNITO_ACCESS_TOKEN", "").strip()
_POOL = os.environ.get("EDA_COGNITO_USER_POOL_ID", "").strip()
_CLIENT = os.environ.get("EDA_COGNITO_CLIENT_ID", "").strip()
_REGION = os.environ.get("EDA_COGNITO_REGION", "us-east-1").strip()

_SKIP = not (_INTEGRATION_TOKEN and _POOL and _CLIENT)
_SKIP_REASON = (
    "Set EDA_INTEGRATION_COGNITO_ACCESS_TOKEN, EDA_COGNITO_USER_POOL_ID, "
    "and EDA_COGNITO_CLIENT_ID (optional EDA_COGNITO_REGION) to run this test."
)


@pytest.fixture
def live_oauth_http_main(monkeypatch):
    pytest.importorskip("jwt", reason="Install oauth extra: pip install easydeploy-ai-mcp[oauth]")

    monkeypatch.setenv("EDA_OAUTH_ENABLED", "1")
    monkeypatch.setenv("EDA_COGNITO_USER_POOL_ID", _POOL)
    monkeypatch.setenv("EDA_COGNITO_CLIENT_ID", _CLIENT)
    monkeypatch.setenv("EDA_COGNITO_REGION", _REGION)
    monkeypatch.delenv("MCP_SERVICE_TOKEN", raising=False)

    import easydeploy_ai_mcp.auth as auth_mod
    import easydeploy_ai_mcp.http_main as http_main

    auth_mod._jwk_client.cache_clear()
    importlib.reload(http_main)

    yield http_main

    monkeypatch.delenv("EDA_OAUTH_ENABLED", raising=False)
    monkeypatch.delenv("EDA_COGNITO_USER_POOL_ID", raising=False)
    monkeypatch.delenv("EDA_COGNITO_CLIENT_ID", raising=False)
    monkeypatch.delenv("EDA_COGNITO_REGION", raising=False)
    auth_mod._jwk_client.cache_clear()
    importlib.reload(http_main)


def _mcp_session_id(response) -> str | None:
    for key, value in response.headers.items():
        if key.lower() == "mcp-session-id":
            return value
    return None


@pytest.mark.integration
@pytest.mark.skipif(_SKIP, reason=_SKIP_REASON)
def test_real_cognito_jwt_mcp_initialize_and_tools_list(live_oauth_http_main):
    token = _INTEGRATION_TOKEN
    init_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "integration-test", "version": "0"},
        },
    }
    with TestClient(live_oauth_http_main.app) as client:
        r1 = client.post(
            "/mcp",
            json=init_body,
            headers={
                "Accept": MCP_ACCEPT,
                "Authorization": f"Bearer {token}",
            },
        )
        assert r1.status_code == 200, f"initialize failed: {r1.status_code} {r1.text[:500]}"
        sid = _mcp_session_id(r1)
        assert sid, f"missing mcp-session-id header: {dict(r1.headers)}"

        list_body = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        r2 = client.post(
            "/mcp",
            json=list_body,
            headers={
                "Accept": MCP_ACCEPT,
                "Authorization": f"Bearer {token}",
                "Mcp-Session-Id": sid,
            },
        )
    assert r2.status_code == 200, f"tools/list failed: {r2.status_code} {r2.text[:500]}"
    assert "get_account_status" in r2.text, r2.text[:800]
