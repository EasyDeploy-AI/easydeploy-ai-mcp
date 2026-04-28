"""Console entry points for stdio (local) and HTTP (remote) MCP."""

from __future__ import annotations


def run_stdio() -> None:
    """Run the EasyDeploy AI MCP server over stdio (Claude Desktop / Cursor local config)."""
    from easydeploy_ai_mcp.server import mcp

    mcp.run()


def run_http() -> None:
    """Run the EasyDeploy AI MCP server over HTTP (uvicorn, port from PORT env, default 8080)."""
    import os

    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    trust = os.environ.get("EDA_TRUST_FORWARDED_HEADERS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    uvicorn.run(
        "easydeploy_ai_mcp.http_main:app",
        host=host,
        port=port,
        factory=False,
        proxy_headers=True,
        forwarded_allow_ips="*" if trust else "127.0.0.1",
    )
