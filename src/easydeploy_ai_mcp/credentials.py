"""
credentials.py — Resolve the bearer token used for outbound EasyDeploy API calls.

There are two transport surfaces:

* **stdio (local)** — the user's machine sets ``EDA_API_KEY`` in env. The MCP
  server is single-tenant and uses that key for every outbound call.

* **HTTP + OAuth** (``EDA_OAUTH_ENABLED=1``) — each MCP request must carry
  ``Authorization: Bearer <jwt|eda_live_…>``. Middleware stashes it on
  ``request.state.bearer_token``. There is **no** fallback to ``EDA_API_KEY``
  for outbound calls (multi-tenant safety).

* **HTTP without OAuth** — same resolver can use per-request bearer if present,
  else ``EDA_API_KEY`` / ``env_fallback`` (legacy or open dev).

Tools call ``resolve_bearer_token`` and do not branch on transport.
"""

from __future__ import annotations

import os

from .auth import AuthError, is_oauth_enabled


_REQUEST_STATE_KEY = "bearer_token"


def resolve_bearer_token(env_fallback: str = "") -> str:
    """Return the bearer token to use for the current outbound API call.

    Resolution order when **OAuth is enabled** (``EDA_OAUTH_ENABLED=1``):

        1. ``request.state.bearer_token`` only (JWT or ``eda_live_*`` from the
           incoming MCP request). **No** ``EDA_API_KEY`` or ``env_fallback``.

    Resolution order when OAuth is **disabled**:

        1. ``request.state.bearer_token`` if present (e.g. ungated HTTP dev)
        2. ``env_fallback`` (module-level ``_API_KEY`` from ``EDA_API_KEY`` at import)
        3. ``EDA_API_KEY`` env var

    Raises ``AuthError`` if no credential is available.
    """
    token = _token_from_request_context()
    if token:
        return token
    if is_oauth_enabled():
        raise AuthError(
            "OAuth mode does not use EDA_API_KEY for outbound calls. "
            "Send Authorization: Bearer <Cognito access token or eda_live_…> on the MCP request.",
            status_code=401,
        )
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
