"""OAuth authorization-server metadata proxy for MCP OAuth discovery.

Some MCP clients resolve ``authorization_servers`` from RFC 9728 resource
metadata, then fetch ``GET {issuer}/.well-known/oauth-authorization-server``.
AWS Cognito does **not** serve that URL on the pool issuer
(``https://cognito-idp.<region>.amazonaws.com/<pool_id>``) — it returns **400**.
Cognito *does* publish ``/.well-known/openid-configuration`` at the same host.

We advertise the **MCP host** as the authorization server in resource metadata and
serve RFC 8414-style metadata on the MCP origin. The published
``authorization_endpoint`` and ``token_endpoint`` are **on the MCP host**
(``/authorize`` and ``/token``); HTTP handlers forward to Cognito using the real
URLs from Cognito's OIDC document. That ensures **every** client (including
spec-compliant ones that would otherwise call Cognito directly) hits the proxy,
where we can drop parameters Cognito rejects (e.g. RFC 8707 ``resource`` from
MCP brokers). ``revocation_endpoint`` remains Cognito's URL when present.
**Access tokens are still issued by Cognito** (``iss`` = pool issuer); JWT
verification is unchanged.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import httpx


class OidcFetchError(Exception):
    """Raised when Cognito OIDC discovery fails."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@lru_cache(maxsize=8)
def fetch_cognito_openid_configuration_json(cognito_issuer: str) -> dict[str, Any]:
    """GET Cognito ``openid-configuration`` (cached)."""
    url = f"{cognito_issuer.rstrip('/')}/.well-known/openid-configuration"
    try:
        resp = httpx.get(url, timeout=15.0)
    except httpx.RequestError as e:
        raise OidcFetchError(f"OIDC discovery request failed: {e}") from e
    if resp.status_code != 200:
        raise OidcFetchError(
            f"OIDC discovery returned HTTP {resp.status_code}",
            status_code=502,
        )
    try:
        data = resp.json()
    except ValueError as e:
        raise OidcFetchError(f"OIDC discovery returned invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise OidcFetchError("OIDC discovery JSON must be an object", status_code=502)
    return data


def build_proxy_authorization_server_metadata(
    *,
    mcp_issuer: str,
    cognito_oidc: dict[str, Any],
) -> dict[str, Any]:
    """RFC 8414-style document for ``/.well-known/oauth-authorization-server``."""
    mcp_issuer = mcp_issuer.rstrip("/")
    authz = cognito_oidc.get("authorization_endpoint")
    token_ep = cognito_oidc.get("token_endpoint")
    if not isinstance(authz, str) or not isinstance(token_ep, str):
        raise OidcFetchError(
            "Cognito OIDC document missing authorization_endpoint or token_endpoint",
            status_code=502,
        )
    raw_methods = cognito_oidc.get("token_endpoint_auth_methods_supported")
    methods: list[str]
    if isinstance(raw_methods, list):
        methods = [str(m) for m in raw_methods]
    else:
        methods = []
    if "none" not in methods:
        # Public app clients (PKCE, no secret) use this at the token endpoint.
        methods = ["none", *methods]

    body: dict[str, Any] = {
        "issuer": mcp_issuer,
        "authorization_endpoint": f"{mcp_issuer}/authorize",
        "token_endpoint": f"{mcp_issuer}/token",
        # RFC 7591 — Cognito has no native DCR; brokers still expect this for MCP OAuth.
        # Actual handler returns the pre-created ``easydeploy-mcp-claude-oauth`` client id.
        "registration_endpoint": f"{mcp_issuer}/oauth/register",
        "response_types_supported": ["code"],
        "scopes_supported": ["openid", "email", "profile"],
        "token_endpoint_auth_methods_supported": methods,
        "code_challenge_methods_supported": ["S256"],
    }
    rev = cognito_oidc.get("revocation_endpoint")
    if isinstance(rev, str) and rev:
        body["revocation_endpoint"] = rev
    return body
