"""Tests for OAuth resource-server primitives and HTTP middleware.

Uses a locally-generated RSA keypair to sign tokens and a stub PyJWKClient
that returns the matching public key, so we exercise the real pyjwt
verification path without hitting Cognito.
"""

from __future__ import annotations

import importlib
import time
from types import SimpleNamespace
from typing import Iterator

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from easydeploy_ai_mcp import auth


USER_POOL_ID = "us-east-1_TESTPOOL"
CLIENT_ID = "test-client-id"
REGION = "us-east-1"
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}"


@pytest.fixture(scope="module")
def rsa_keypair() -> tuple[rsa.RSAPrivateKey, bytes]:
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv, pub_pem


def _sign(priv: rsa.RSAPrivateKey, claims: dict) -> str:
    return jwt.encode(claims, priv, algorithm="RS256", headers={"kid": "test-kid"})


def _good_claims() -> dict:
    now = int(time.time())
    return {
        "iss": ISSUER,
        "client_id": CLIENT_ID,
        "token_use": "access",
        "sub": "user-123",
        "exp": now + 600,
        "iat": now,
    }


@pytest.fixture
def patch_jwks(rsa_keypair, monkeypatch) -> Iterator[None]:
    _, pub_pem = rsa_keypair

    class _StubKey:
        key = pub_pem

    class _StubClient:
        def __init__(self, *_a, **_kw): ...
        def get_signing_key_from_jwt(self, _token):  # noqa: D401
            return _StubKey()

    auth._jwk_client.cache_clear()
    monkeypatch.setattr(auth, "_jwk_client", lambda _uri: _StubClient())
    yield


@pytest.fixture
def cfg() -> auth.OAuthConfig:
    return auth.OAuthConfig(user_pool_id=USER_POOL_ID, client_id=CLIENT_ID, region=REGION)


def test_verify_accepts_valid_access_token(rsa_keypair, patch_jwks, cfg):
    priv, _ = rsa_keypair
    token = _sign(priv, _good_claims())
    claims = auth.verify_cognito_access_token(token, cfg)
    assert claims["client_id"] == CLIENT_ID
    assert claims["token_use"] == "access"


def test_verify_rejects_id_token(rsa_keypair, patch_jwks, cfg):
    priv, _ = rsa_keypair
    claims = _good_claims() | {"token_use": "id"}
    token = _sign(priv, claims)
    with pytest.raises(auth.AuthError, match="token_use"):
        auth.verify_cognito_access_token(token, cfg)


def test_verify_rejects_wrong_client_id(rsa_keypair, patch_jwks, cfg):
    priv, _ = rsa_keypair
    token = _sign(priv, _good_claims() | {"client_id": "someone-else"})
    with pytest.raises(auth.AuthError, match="client_id mismatch"):
        auth.verify_cognito_access_token(token, cfg)


def test_verify_rejects_expired(rsa_keypair, patch_jwks, cfg):
    priv, _ = rsa_keypair
    now = int(time.time())
    token = _sign(priv, _good_claims() | {"exp": now - 10, "iat": now - 600})
    with pytest.raises(auth.AuthError):
        auth.verify_cognito_access_token(token, cfg)


def test_verify_rejects_wrong_issuer(rsa_keypair, patch_jwks, cfg):
    priv, _ = rsa_keypair
    token = _sign(priv, _good_claims() | {"iss": "https://evil.example.com"})
    with pytest.raises(auth.AuthError):
        auth.verify_cognito_access_token(token, cfg)


def test_looks_like_api_key():
    assert auth.looks_like_api_key("eda_live_abc123")
    assert not auth.looks_like_api_key("eyJhbGciOiJSUzI1NiJ9.aaa.bbb")


# ── HTTP middleware integration ─────────────────────────────────────────────


@pytest.fixture
def oauth_app(rsa_keypair, monkeypatch):
    _, pub_pem = pub_pem_pair = rsa_keypair
    monkeypatch.setenv("EDA_OAUTH_ENABLED", "1")
    monkeypatch.setenv("EDA_COGNITO_USER_POOL_ID", USER_POOL_ID)
    monkeypatch.setenv("EDA_COGNITO_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("EDA_COGNITO_REGION", REGION)
    monkeypatch.delenv("MCP_SERVICE_TOKEN", raising=False)

    import easydeploy_ai_mcp.http_main as http_main
    importlib.reload(http_main)

    # Stub JWKS in the reloaded module's auth reference.
    class _StubKey:
        key = pub_pem

    class _StubClient:
        def __init__(self, *_a, **_kw): ...
        def get_signing_key_from_jwt(self, _token):
            return _StubKey()

    http_main.auth._jwk_client.cache_clear()
    monkeypatch.setattr(http_main.auth, "_jwk_client", lambda _uri: _StubClient())

    yield http_main

    monkeypatch.delenv("EDA_OAUTH_ENABLED", raising=False)
    importlib.reload(http_main)


def test_healthz_open_in_oauth_mode(oauth_app):
    with TestClient(oauth_app.app) as client:
        r = client.get("/healthz")
    assert r.status_code == 200


def test_protected_resource_metadata_published(oauth_app):
    with TestClient(oauth_app.app) as client:
        r = client.get("/.well-known/oauth-protected-resource")
    assert r.status_code == 200
    body = r.json()
    assert body["authorization_servers"] == [ISSUER]
    assert "header" in body["bearer_methods_supported"]


def test_mcp_requires_bearer(oauth_app):
    with TestClient(oauth_app.app) as client:
        r = client.post("/mcp", json={})
    assert r.status_code == 401
    assert "Bearer" in r.headers.get("WWW-Authenticate", "")


def test_mcp_rejects_invalid_jwt(oauth_app):
    with TestClient(oauth_app.app) as client:
        r = client.post(
            "/mcp",
            json={},
            headers={"Authorization": "Bearer not-a-real-token"},
        )
    assert r.status_code == 401


def test_mcp_accepts_valid_jwt(oauth_app, rsa_keypair):
    priv, _ = rsa_keypair
    token = _sign(priv, _good_claims())
    with TestClient(oauth_app.app) as client:
        r = client.post(
            "/mcp",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code != 401


def test_mcp_forwards_api_key_without_local_validation(oauth_app):
    with TestClient(oauth_app.app) as client:
        r = client.post(
            "/mcp",
            json={},
            headers={"Authorization": "Bearer eda_live_anything"},
        )
    assert r.status_code != 401


def test_oauth_and_service_token_are_mutually_exclusive(monkeypatch):
    monkeypatch.setenv("EDA_OAUTH_ENABLED", "1")
    monkeypatch.setenv("EDA_COGNITO_USER_POOL_ID", USER_POOL_ID)
    monkeypatch.setenv("EDA_COGNITO_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("MCP_SERVICE_TOKEN", "secret")
    import easydeploy_ai_mcp.http_main as http_main
    with pytest.raises(RuntimeError, match="mutually exclusive"):
        importlib.reload(http_main)
    monkeypatch.delenv("EDA_OAUTH_ENABLED", raising=False)
    monkeypatch.delenv("MCP_SERVICE_TOKEN", raising=False)
    importlib.reload(http_main)
