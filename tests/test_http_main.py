"""ASGI app: healthz and optional MCP_SERVICE_TOKEN gate."""

from __future__ import annotations

import importlib

import pytest
from starlette.testclient import TestClient


def test_healthz_returns_ok():
    from easydeploy_ai_mcp.http_main import app

    with TestClient(app) as client:
        r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_mcp_path_requires_bearer_when_token_set(monkeypatch):
    monkeypatch.setenv("MCP_SERVICE_TOKEN", "secret-token")
    import easydeploy_ai_mcp.http_main as http_main

    importlib.reload(http_main)

    with TestClient(http_main.app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.post("/mcp", json={}).status_code == 401
        r_ok = client.post(
            "/mcp",
            json={},
            headers={"Authorization": "Bearer secret-token"},
        )
        assert r_ok.status_code != 401

    monkeypatch.delenv("MCP_SERVICE_TOKEN", raising=False)
    importlib.reload(http_main)
