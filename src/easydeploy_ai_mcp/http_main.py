"""
ASGI entrypoint for remote MCP over HTTP (Streamable HTTP via FastMCP).

Routes:
    GET  /healthz                              — ALB / container health (no auth)
    GET  /.well-known/oauth-protected-resource — RFC 9728 metadata (OAuth mode only)
    /mcp                                       — MCP endpoint (FastMCP http_app)

Auth modes (mutually exclusive):

* **OAuth resource server** (``EDA_OAUTH_ENABLED=1``)
    Requires ``EDA_COGNITO_USER_POOL_ID`` and ``EDA_COGNITO_CLIENT_ID``.
    Each MCP request must carry ``Authorization: Bearer <token>`` where the
    token is either:
      - a Cognito access JWT (verified locally against the Cognito JWKS), or
      - an EasyDeploy API key (``eda_live_*`` prefix; forwarded to the API
        which is the source of truth for revocation/expiry).
    The verified token is stashed on ``request.state.bearer_token`` and
    forwarded verbatim to the EasyDeploy REST API by tool calls.

* **Shared-secret gate** (``MCP_SERVICE_TOKEN`` set, OAuth disabled)
    Legacy single-tenant deployments. All requests except ``/healthz``
    require ``Authorization: Bearer <MCP_SERVICE_TOKEN>``. Tool calls then
    use the static ``EDA_API_KEY`` env var.

* **No auth** (neither set)
    Development only. Do not deploy publicly.

Setting both ``EDA_OAUTH_ENABLED`` and ``MCP_SERVICE_TOKEN`` is a
configuration error and raises at import time — the shared-secret gate
would shadow the per-user JWT and the inner credential resolver would never
see the right token.
"""

from __future__ import annotations

import json
import os

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from easydeploy_ai_mcp import auth
from easydeploy_ai_mcp.server import mcp


_OAUTH_ENABLED = auth.is_oauth_enabled()
_SERVICE_TOKEN = os.environ.get("MCP_SERVICE_TOKEN", "").strip()

if _OAUTH_ENABLED and _SERVICE_TOKEN:
    raise RuntimeError(
        "MCP_SERVICE_TOKEN and EDA_OAUTH_ENABLED are mutually exclusive. "
        "The shared-secret gate would consume the Authorization header before "
        "OAuth validation runs. Pick one."
    )

_OAUTH_CONFIG = auth.load_oauth_config() if _OAUTH_ENABLED else None


def _bearer_from_header(request: Request) -> str:
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return ""
    return auth_header[7:].strip()


def _www_authenticate(error: str = "") -> str:
    parts = ['Bearer realm="EasyDeploy MCP"']
    if _OAUTH_CONFIG is not None:
        parts.append(f'as_uri="{_OAUTH_CONFIG.issuer}"')
    if error:
        parts.append(f'error="{error}"')
    return ", ".join(parts)


def _unauthorized(detail: str, error: str = "invalid_token") -> JSONResponse:
    return JSONResponse(
        {"detail": detail},
        status_code=401,
        headers={"WWW-Authenticate": _www_authenticate(error)},
    )


async def healthz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def oauth_protected_resource_metadata(request: Request) -> Response:
    """RFC 9728 — protected resource metadata. Lets MCP clients discover the
    Cognito authorization server from a 401 response."""
    if _OAUTH_CONFIG is None:
        return JSONResponse({"detail": "OAuth not configured"}, status_code=404)
    body = {
        "resource": f"{request.url.scheme}://{request.url.netloc}/mcp",
        "authorization_servers": [_OAUTH_CONFIG.issuer],
        "bearer_methods_supported": ["header"],
        "scopes_supported": [],
    }
    return Response(
        content=json.dumps(body),
        media_type="application/json",
    )


class _ServiceTokenMiddleware(BaseHTTPMiddleware):
    """Legacy shared-secret gate. Used only when OAuth is disabled."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        if _is_open_path(request.url.path):
            return await call_next(request)
        if not _SERVICE_TOKEN:
            return await call_next(request)
        if _bearer_from_header(request) != _SERVICE_TOKEN:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)


class _OAuthResourceServerMiddleware(BaseHTTPMiddleware):
    """OAuth 2.0 resource server gate.

    * Cognito JWTs are validated locally (issuer, signature, exp, client_id,
      token_use=='access') against the Cognito JWKS.
    * API keys (``eda_live_*``) are NOT validated locally — the downstream
      REST API is the source of truth. They are forwarded as-is.

    On success the raw token is placed on ``request.state.bearer_token`` so
    the credential resolver can forward it to outbound API calls.
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        if _is_open_path(request.url.path):
            return await call_next(request)
        token = _bearer_from_header(request)
        if not token:
            return _unauthorized("Missing bearer token")
        if not auth.looks_like_api_key(token):
            assert _OAUTH_CONFIG is not None  # guaranteed by enabled flag
            try:
                auth.verify_cognito_access_token(token, _OAUTH_CONFIG)
            except auth.AuthError as e:
                return _unauthorized(str(e))
        request.state.bearer_token = token
        return await call_next(request)


def _is_open_path(path: str) -> bool:
    p = path.rstrip("/") or "/"
    return p in {"/healthz", "/.well-known/oauth-protected-resource"}


_mcp_http = mcp.http_app()

_routes: list = [
    Route("/healthz", endpoint=healthz, methods=["GET"]),
]
if _OAUTH_ENABLED:
    _routes.append(
        Route(
            "/.well-known/oauth-protected-resource",
            endpoint=oauth_protected_resource_metadata,
            methods=["GET"],
        )
    )
_routes.append(Mount("/", app=_mcp_http, name="mcp_root"))

app = Starlette(routes=_routes, lifespan=_mcp_http.lifespan)
if _OAUTH_ENABLED:
    app.add_middleware(_OAuthResourceServerMiddleware)
else:
    app.add_middleware(_ServiceTokenMiddleware)
