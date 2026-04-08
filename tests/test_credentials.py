"""Credential resolver: env fallback + AuthError when nothing is set."""

from __future__ import annotations

import pytest

from easydeploy_ai_mcp import credentials
from easydeploy_ai_mcp.auth import AuthError


def test_resolve_uses_env_fallback(monkeypatch):
    monkeypatch.delenv("EDA_API_KEY", raising=False)
    assert credentials.resolve_bearer_token(env_fallback="static-key") == "static-key"


def test_resolve_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("EDA_API_KEY", "from-env")
    assert credentials.resolve_bearer_token(env_fallback="") == "from-env"


def test_resolve_raises_when_no_credential(monkeypatch):
    monkeypatch.delenv("EDA_API_KEY", raising=False)
    with pytest.raises(AuthError):
        credentials.resolve_bearer_token(env_fallback="")
