"""
credentials.py — Resolve the bearer token used for outbound EasyDeploy API calls.

There are two transport surfaces:

* **stdio (local)** — the user's machine sets ``EDA_API_KEY`` in env. The MCP
  server is single-tenant and uses that key for every outbound call.
* **HTTP (remote)** — when ``EDA_OAUTH_ENABLED=1``, the MCP server is a
  multi-tenant OAuth resource server. Each request carries its own
  ``Authorization: Bearer <jwt|api_key>``. The HTTP middleware validates and
  stores the raw token on ``request.state.bearer_token``; this resolver
  surfaces it to tools via FastMCP's request-context dependency.

The fallback chain is intentionally simple: per-request token first, then
env. Tools never branch on transport — they just call ``resolve_bearer_token``.
"""

from __future__ import annotations

import os

from .auth import AuthError


_REQUEST_STATE_KEY = "bearer_token"


def resolve_bearer_token(env_fallback: str = "") -> str:
    """Return the bearer token to use for the current outbound API call.

    Resolution order:
        1. ``request.state.bearer_token`` (set by ``OAuthResourceServerMiddleware``)
        2. ``env_fallback`` (typically the module-level ``_API_KEY``)
        3. ``EDA_API_KEY`` env var

    Raises ``AuthError`` if no credential is available. The HTTP path returns
    401 in that case; the stdio path surfaces it as a tool error so the user
    sees a clear "set EDA_API_KEY" message instead of an opaque 401 from the
    API later.
    """
    token = _token_from_request_context()
    if token:
        return token
    if env_fallback:
        return env_fallback
    env_token = os.environ.get("EDA_API_KEY", "").strip()
    if env_token:
        return env_token
    raise AuthError(
        "No bearer token available. Set EDA_API_KEY (stdio) or send "
        "Authorization: Bearer <token> (HTTP).",
        status_code=401,
    )


def _token_from_request_context() -> str:
    """Best-effort: read the per-request bearer token if we are inside an
    HTTP-transport call. Returns "" otherwise (stdio, or no middleware)."""
    try:
        from fastmcp.server.dependencies import get_http_request  # type: ignore[import-not-found]
    except Exception:
        return ""
    try:
        request = get_http_request()
    except Exception:
        return ""
    if request is None:
        return ""
    token = getattr(request.state, _REQUEST_STATE_KEY, None)
    return token if isinstance(token, str) and token else ""
