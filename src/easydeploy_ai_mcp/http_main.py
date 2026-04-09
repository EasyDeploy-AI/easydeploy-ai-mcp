"""
ASGI entrypoint for remote MCP over HTTP (Streamable HTTP via FastMCP).

Routes:
    GET/HEAD/POST ``/`` — redirect ``307`` to ``/mcp`` (connector base URL without path)
    GET  /healthz — ALB / container health (no auth)
    GET  /.well-known/oauth-protected-resource (+ ``/mcp`` suffix) — RFC 9728 resource metadata
    GET  /.well-known/oauth-authorization-server — RFC 8414 metadata **on the MCP host** (published
        ``/authorize`` and ``/token`` on this origin; handlers forward to Cognito; Cognito's issuer URL does
        not serve ``oauth-authorization-server`` and returns 400 for strict MCP brokers)
    GET  /authorize — **302** to Cognito ``oauth2/authorize`` with query forwarded (Claude.ai broker quirk; see below)
    GET/POST /token — reverse-proxy to Cognito ``oauth2/token`` (same quirk; some Claude.ai builds use **GET** with query params per `claude-ai-mcp#82`)
    POST /register — same static DCR as ``/oauth/register`` (brokers often POST issuer-relative ``/register``)
    POST /oauth/register — static RFC 7591-style DCR (returns ``EDA_COGNITO_CLIENT_ID``; Cognito has no real DCR)

    **Claude.ai (``mcp_token_exchange_failed``):** some builds call ``POST {mcp_origin}/token`` instead of the
    ``token_endpoint`` from AS metadata (e.g. `anthropics/claude-ai-mcp#82`). Root ``/token`` and ``/authorize``
    proxy to the URLs from Cognito's ``openid-configuration``.
    GET/HEAD /mcp — in OAuth mode, unauthenticated **200** (reachability for Claude web); POST still requires bearer
    /mcp — MCP JSON-RPC (FastMCP http_app); POST requires ``Authorization: Bearer``

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
    The ``EDA_API_KEY`` environment variable is **not** used as a fallback
    for outbound calls in this mode.

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

import asyncio
import json
import os
import time
import urllib.parse

import httpx
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route

from easydeploy_ai_mcp import auth
from easydeploy_ai_mcp import oauth_as_metadata
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


def _mcp_oauth_issuer(request: Request) -> str:
    """Issuer URL advertised as ``authorization_servers`` and in proxy AS metadata.

    Defaults to the incoming request origin (``scheme`` + ``netloc``). Override with
    ``EDA_MCP_OAUTH_ISSUER`` when the MCP sits behind a reverse proxy that does not
    set ``X-Forwarded-Proto`` / ``Host`` correctly.
    """
    override = os.environ.get("EDA_MCP_OAUTH_ISSUER", "").strip()
    if override:
        return override.rstrip("/")
    return f"{request.url.scheme}://{request.url.netloc}".rstrip("/")


def _protected_resource_metadata_url(request: Request) -> str:
    """RFC 9728 metadata URL for resource ``https://<host>/mcp``."""
    return (
        f"{request.url.scheme}://{request.url.netloc}"
        "/.well-known/oauth-protected-resource/mcp"
    )


def _www_authenticate(request: Request, *, error: str = "") -> str:
    """RFC 9728 §5.1: ``resource_metadata`` only (no non-standard params that break strict parsers)."""
    parts = ['Bearer realm="EasyDeploy MCP"']
    if _OAUTH_CONFIG is not None:
        meta = _protected_resource_metadata_url(request)
        parts.append(f'resource_metadata="{meta}"')
    if error:
        parts.append(f'error="{error}"')
    return ", ".join(parts)


def _unauthorized(request: Request, detail: str, *, error: str = "") -> JSONResponse:
    return JSONResponse(
        {"detail": detail},
        status_code=401,
        headers={"WWW-Authenticate": _www_authenticate(request, error=error)},
    )


async def healthz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def oauth_protected_resource_metadata(request: Request) -> Response:
    """RFC 9728 — protected resource metadata.

    For resource ``https://<host>/mcp``, the metadata URL is
    ``/.well-known/oauth-protected-resource/mcp`` (not only the host-level path).
    MCP clients (e.g. Claude) fetch that URL without a bearer; it must bypass
    the OAuth gate.
    """
    if _OAUTH_CONFIG is None:
        return JSONResponse({"detail": "OAuth not configured"}, status_code=404)
    # Match Cognito app client ``easydeploy-mcp-claude-oauth`` (Amplify backend.ts):
    # openid, email, profile. Empty scopes_supported confused some MCP OAuth brokers.
    body = {
        "resource": f"{request.url.scheme}://{request.url.netloc}/mcp",
        # MCP host — not Cognito's issuer. Brokers fetch
        # ``/.well-known/oauth-authorization-server`` here; Cognito does not serve
        # that path on cognito-idp.* and returns 400.
        "authorization_servers": [_mcp_oauth_issuer(request)],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["openid", "email", "profile"],
    }
    return Response(
        content=json.dumps(body),
        media_type="application/json",
    )


async def redirect_root_to_mcp(_request: Request) -> Response:
    """Claude sometimes uses the MCP origin without ``/mcp``; MCP lives at ``/mcp``."""
    return RedirectResponse(url="/mcp", status_code=307)


async def oauth_authorization_server_metadata(request: Request) -> Response:
    """RFC 8414-style metadata at the MCP origin; endpoints point at Cognito Hosted UI / token."""
    if _OAUTH_CONFIG is None:
        return JSONResponse({"detail": "OAuth not configured"}, status_code=404)
    mcp_iss = _mcp_oauth_issuer(request)
    try:
        oidc = await asyncio.to_thread(
            oauth_as_metadata.fetch_cognito_openid_configuration_json,
            _OAUTH_CONFIG.issuer,
        )
        body = oauth_as_metadata.build_proxy_authorization_server_metadata(
            mcp_issuer=mcp_iss,
            cognito_oidc=oidc,
        )
    except oauth_as_metadata.OidcFetchError as e:
        return JSONResponse({"detail": e.message}, status_code=e.status_code)
    return Response(
        content=json.dumps(body),
        media_type="application/json",
    )


async def oauth_static_client_registration(request: Request) -> JSONResponse:
    """RFC 7591-style registration response using the existing Cognito public app client.

    Cognito does not implement dynamic client registration. MCP OAuth brokers still
    expect ``registration_endpoint`` and a ``client_id``; we always return the pool's
    pre-configured MCP OAuth client (``EDA_COGNITO_CLIENT_ID``).
    """
    assert _OAUTH_CONFIG is not None
    reg_body: dict = {}
    try:
        raw = await request.body()
        if raw:
            parsed = json.loads(raw.decode())
            if isinstance(parsed, dict):
                reg_body = parsed
    except (json.JSONDecodeError, UnicodeDecodeError):
        reg_body = {}

    redirect_uris = reg_body.get("redirect_uris")
    if not isinstance(redirect_uris, list):
        redirect_uris = []
    client_name = reg_body.get("client_name")
    if not isinstance(client_name, str) or not client_name.strip():
        client_name = "mcp-oauth-client"

    issued_at = int(time.time())
    payload = {
        "client_id": _OAUTH_CONFIG.client_id,
        "client_id_issued_at": issued_at,
        "client_name": client_name,
        "redirect_uris": redirect_uris,
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": "openid email profile",
    }
    return JSONResponse(status_code=201, content=payload)


async def _cognito_oidc_dict() -> dict:
    assert _OAUTH_CONFIG is not None
    return await asyncio.to_thread(
        oauth_as_metadata.fetch_cognito_openid_configuration_json,
        _OAUTH_CONFIG.issuer,
    )


def _strip_resource_query_param(query_string: str) -> str:
    """Drop ``resource`` (RFC 8707); Cognito Hosted UI / token often reject broker-sent values."""
    if not query_string:
        return query_string
    pairs = urllib.parse.parse_qsl(query_string, keep_blank_values=True)
    filtered = [(k, v) for k, v in pairs if k != "resource"]
    return urllib.parse.urlencode(filtered)


def _form_body_without_resource(body: bytes, content_type: str | None) -> bytes:
    """Remove ``resource`` from ``application/x-www-form-urlencoded`` token requests."""
    ct = (content_type or "").split(";")[0].strip().lower()
    if not body or ct != "application/x-www-form-urlencoded":
        return body
    try:
        text = body.decode()
    except UnicodeDecodeError:
        return body
    pairs = urllib.parse.parse_qsl(text, keep_blank_values=True)
    filtered = [(k, v) for k, v in pairs if k != "resource"]
    return urllib.parse.urlencode(filtered).encode("ascii")


async def proxy_oauth_authorize(request: Request) -> Response:
    """Redirect to Cognito authorize URL; query string preserved (Claude.ai /token + /authorize quirk)."""
    if _OAUTH_CONFIG is None:
        return JSONResponse({"detail": "OAuth not configured"}, status_code=404)
    try:
        oidc = await _cognito_oidc_dict()
    except oauth_as_metadata.OidcFetchError as e:
        return JSONResponse({"detail": e.message}, status_code=e.status_code)
    authz = oidc.get("authorization_endpoint")
    if not isinstance(authz, str) or not authz:
        return JSONResponse({"detail": "Missing authorization_endpoint"}, status_code=502)
    q = _strip_resource_query_param(request.url.query)
    target = f"{authz}?{q}" if q else authz
    return RedirectResponse(url=target, status_code=302)


async def proxy_oauth_token(request: Request) -> Response:
    """Forward token request to Cognito (Claude.ai may POST or GET ``/token`` vs metadata).

    OAuth 2.0 uses POST form body; Cognito's token endpoint expects POST. Some Claude.ai
    builds incorrectly issue **GET /token?grant_type=...** — we convert query params to a
    form body and POST upstream.
    """
    if _OAUTH_CONFIG is None:
        return JSONResponse({"detail": "OAuth not configured"}, status_code=404)
    try:
        oidc = await _cognito_oidc_dict()
    except oauth_as_metadata.OidcFetchError as e:
        return JSONResponse({"detail": e.message}, status_code=e.status_code)
    token_ep = oidc.get("token_endpoint")
    if not isinstance(token_ep, str) or not token_ep:
        return JSONResponse({"detail": "Missing token_endpoint"}, status_code=502)

    headers: dict[str, str] = {}
    if request.method == "GET":
        pairs = [(k, v) for k, v in request.query_params.multi_items() if k != "resource"]
        body = urllib.parse.urlencode(pairs).encode("ascii")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    else:
        raw = await request.body()
        ct_in = request.headers.get("content-type")
        body = _form_body_without_resource(raw, ct_in)
        if ct_in:
            headers["Content-Type"] = ct_in.split(";")[0].strip()
        elif body:
            headers["Content-Type"] = "application/x-www-form-urlencoded"

    if auth_h := request.headers.get("authorization"):
        headers["Authorization"] = auth_h

    timeout = httpx.Timeout(30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(token_ep, content=body, headers=headers)
        except httpx.RequestError as e:
            return JSONResponse(
                {"detail": f"Token proxy upstream error: {e}"},
                status_code=502,
            )
    out: dict[str, str] = {}
    if resp_ct := resp.headers.get("content-type"):
        out["Content-Type"] = resp_ct
    return Response(content=resp.content, status_code=resp.status_code, headers=out)


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
        if _oauth_mcp_browser_reachability_probe(request):
            if request.method == "HEAD":
                return Response(status_code=200)
            return JSONResponse(
                {
                    "service": "easydeploy-ai-mcp",
                    "mcp_endpoint": True,
                    "authentication": "oauth2",
                },
                status_code=200,
            )
        token = _bearer_from_header(request)
        if not token:
            return _unauthorized(request, "Missing bearer token")
        if not auth.looks_like_api_key(token):
            assert _OAUTH_CONFIG is not None  # guaranteed by enabled flag
            try:
                auth.verify_cognito_access_token(token, _OAUTH_CONFIG)
            except auth.AuthError as e:
                return _unauthorized(request, str(e), error="invalid_token")
        request.state.bearer_token = token
        return await call_next(request)


def _normalized_path(path: str) -> str:
    return path.rstrip("/") or "/"


def _oauth_mcp_browser_reachability_probe(request: Request) -> bool:
    """Claude web (UA Claude-User) GET/HEADs ``/mcp`` without a bearer before OAuth.

    A 401 on that probe is surfaced as "Couldn't reach the MCP server". Unauthenticated
    GET/HEAD are harmless and only advertise that the endpoint exists; POST still requires OAuth.
    """
    if not _OAUTH_ENABLED:
        return False
    if _normalized_path(request.url.path) != "/mcp":
        return False
    if request.method not in ("GET", "HEAD"):
        return False
    return not _bearer_from_header(request)


def _is_open_path(path: str) -> bool:
    p = _normalized_path(path)
    if p == "/healthz":
        return True
    if p == "/.well-known/oauth-protected-resource":
        return True
    # RFC 9728: resource https://host/mcp → this path (Claude httpx probes it).
    if p == "/.well-known/oauth-protected-resource/mcp":
        return True
    # Connector URL without /mcp — redirect handler must bypass the bearer gate.
    if p == "/":
        return True
    if _OAUTH_ENABLED:
        if p == "/.well-known/oauth-authorization-server":
            return True
        if p == "/register":
            return True
        if p == "/oauth/register":
            return True
        if p == "/authorize":
            return True
        if p == "/token":
            return True
    return False


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
    _routes.append(
        Route(
            "/.well-known/oauth-protected-resource/mcp",
            endpoint=oauth_protected_resource_metadata,
            methods=["GET"],
        )
    )
    _routes.append(
        Route(
            "/.well-known/oauth-authorization-server",
            endpoint=oauth_authorization_server_metadata,
            methods=["GET"],
        )
    )
    _routes.append(
        Route(
            "/authorize",
            endpoint=proxy_oauth_authorize,
            methods=["GET"],
        )
    )
    _routes.append(
        Route(
            "/token",
            endpoint=proxy_oauth_token,
            methods=["GET", "POST"],
        )
    )
    _routes.append(
        Route(
            "/oauth/register",
            endpoint=oauth_static_client_registration,
            methods=["POST"],
        )
    )
    _routes.append(
        Route(
            "/register",
            endpoint=oauth_static_client_registration,
            methods=["POST"],
        )
    )
# Root redirect last before catch-all Mount so /healthz and /.well-known win.
_routes.append(
    Route("/", endpoint=redirect_root_to_mcp, methods=["GET", "HEAD", "POST"])
)
_routes.append(Mount("/", app=_mcp_http, name="mcp_root"))

app = Starlette(routes=_routes, lifespan=_mcp_http.lifespan)
if _OAUTH_ENABLED:
    app.add_middleware(_OAuthResourceServerMiddleware)
else:
    app.add_middleware(_ServiceTokenMiddleware)

# Outer layer: Claude web may probe the MCP origin from the browser; without
# CORS, preflight/401 responses are invisible to JS and show as "couldn't reach".
_CLAUDE_BROWSER_ORIGINS = (
    "https://claude.ai",
    "https://www.claude.ai",
    "https://claude.com",
    "https://www.claude.com",
)
_extra_cors = os.environ.get("EDA_CORS_EXTRA_ORIGINS", "").strip()
_cors_origins = list(_CLAUDE_BROWSER_ORIGINS)
if _extra_cors:
    _cors_origins.extend(o.strip() for o in _extra_cors.split(",") if o.strip())

# Browsers do not expose WWW-Authenticate to fetch() unless named here; without it,
# Claude web can see POST /mcp 401 as an opaque failure ("couldn't reach") instead
# of starting OAuth. ``*`` for expose is not reliably honored across browsers.
_CORS_EXPOSE_HEADERS = [
    "WWW-Authenticate",
    "Mcp-Session-Id",
    "mcp-session-id",
    "Content-Type",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=True,
    expose_headers=_CORS_EXPOSE_HEADERS,
)
