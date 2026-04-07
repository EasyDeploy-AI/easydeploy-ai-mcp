"""
ASGI entrypoint for remote MCP over HTTP (Streamable HTTP via FastMCP).

- GET /healthz — ALB/container health checks (no auth).
- /mcp — MCP endpoint (see FastMCP http_app routes).

If MCP_SERVICE_TOKEN is set, all requests except GET /healthz require
``Authorization: Bearer <MCP_SERVICE_TOKEN>``.
"""

from __future__ import annotations

import os

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from easydeploy_ai_mcp.server import mcp


async def healthz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


class _ServiceTokenMiddleware(BaseHTTPMiddleware):
    """Optional shared secret for the MCP HTTP surface (not /healthz)."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path.rstrip("/") or "/"
        if path == "/healthz":
            return await call_next(request)
        token = os.environ.get("MCP_SERVICE_TOKEN", "").strip()
        if not token:
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {token}":
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)


_mcp_http = mcp.http_app()
app = Starlette(
    routes=[
        Route("/healthz", endpoint=healthz, methods=["GET"]),
        Mount("/", app=_mcp_http),
    ],
    lifespan=_mcp_http.lifespan,
)
app.add_middleware(_ServiceTokenMiddleware)
