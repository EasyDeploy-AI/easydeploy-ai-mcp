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

    import easydeploy_ai_mcp.oauth_as_metadata as oam

    def _stub_oidc(_issuer: str):
        return {
            "authorization_endpoint": "https://fake-cognito.oauth.example/oauth2/authorize",
            "token_endpoint": "https://fake-cognito.oauth.example/oauth2/token",
            "revocation_endpoint": "https://fake-cognito.oauth.example/oauth2/revoke",
            "token_endpoint_auth_methods_supported": [
                "client_secret_basic",
                "client_secret_post",
            ],
        }

    monkeypatch.setattr(oam, "fetch_cognito_openid_configuration_json", _stub_oidc)

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
    assert body["authorization_servers"] == ["http://testserver"]
    assert "header" in body["bearer_methods_supported"]
    assert body.get("scopes_supported") == ["openid", "email", "profile"]


def test_protected_resource_metadata_rfc9728_path_when_resource_is_slash_mcp(oauth_app):
    """Claude probes GET /.well-known/oauth-protected-resource/mcp (RFC 9728)."""
    with TestClient(oauth_app.app) as client:
        r = client.get("/.well-known/oauth-protected-resource/mcp")
    assert r.status_code == 200
    body = r.json()
    assert body["authorization_servers"] == ["http://testserver"]
    assert body.get("resource", "").endswith("/mcp")


def test_root_redirects_to_mcp(oauth_app):
    with TestClient(oauth_app.app) as client:
        r = client.get("/", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers.get("location") == "/mcp"


def test_oauth_authorization_server_returns_proxy_metadata(oauth_app):
    """Brokers must GET AS metadata on the MCP host; Cognito issuer returns 400 for this path."""
    with TestClient(oauth_app.app) as client:
        r = client.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200
    body = r.json()
    assert body["issuer"] == "http://testserver"
    assert body["authorization_endpoint"] == "http://testserver/authorize"
    assert body["token_endpoint"] == "http://testserver/token"
    assert body["revocation_endpoint"].endswith("/oauth2/revoke")
    assert body["response_types_supported"] == ["code"]
    assert body["scopes_supported"] == ["openid", "email", "profile"]
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert "none" in body["token_endpoint_auth_methods_supported"]
    assert body.get("registration_endpoint") == "http://testserver/oauth/register"


def test_register_root_same_static_dcr_as_oauth_register(oauth_app):
    """Brokers may POST issuer-relative /register (claude-ai-mcp #82-style paths)."""
    with TestClient(oauth_app.app) as client:
        r = client.post("/register", json={"client_name": "x"})
    assert r.status_code == 201
    assert r.json()["client_id"] == CLIENT_ID


def test_oauth_register_returns_static_cognito_client(oauth_app):
    with TestClient(oauth_app.app) as client:
        r = client.post(
            "/oauth/register",
            json={
                "client_name": "Claude",
                "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
            },
        )
    assert r.status_code == 201
    body = r.json()
    assert body["client_id"] == CLIENT_ID
    assert body["token_endpoint_auth_method"] == "none"
    assert body["grant_types"] == ["authorization_code"]
    assert body["response_types"] == ["code"]
    assert "https://claude.ai/api/mcp/auth_callback" in body["redirect_uris"]


def test_oauth_register_accepts_empty_body(oauth_app):
    with TestClient(oauth_app.app) as client:
        r = client.post("/oauth/register", content=b"")
    assert r.status_code == 201
    assert r.json()["client_id"] == CLIENT_ID


def test_proxy_authorize_redirects_to_cognito_with_query(oauth_app):
    with TestClient(oauth_app.app) as client:
        r = client.get(
            "/authorize",
            params={"client_id": "abc", "response_type": "code"},
            follow_redirects=False,
        )
    assert r.status_code == 302
    loc = r.headers.get("location", "")
    assert loc.startswith("https://fake-cognito.oauth.example/oauth2/authorize")
    assert "client_id=abc" in loc
    assert "response_type=code" in loc


def test_proxy_authorize_strips_resource_from_query(oauth_app):
    """RFC 8707 resource is dropped before Cognito authorize (broker quirk)."""
    with TestClient(oauth_app.app) as client:
        r = client.get(
            "/authorize",
            params={
                "client_id": "abc",
                "response_type": "code",
                "resource": "https://mcp.example.com/mcp",
            },
            follow_redirects=False,
        )
    assert r.status_code == 302
    loc = r.headers.get("location", "")
    assert "resource=" not in loc
    assert "client_id=abc" in loc


def test_proxy_token_posts_to_cognito_token_endpoint(oauth_app, monkeypatch):
    captured: dict = {}

    class _FakeResp:
        def __init__(self) -> None:
            self.content = b'{"access_token":"from-cognito","token_type":"Bearer"}'
            self.status_code = 200
            self.headers = {"content-type": "application/json"}

    class _FakeClient:
        def __init__(self, *_a, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def post(self, url, content=b"", headers=None):
            captured["url"] = url
            captured["content"] = content
            captured["headers"] = dict(headers or {})
            return _FakeResp()

    monkeypatch.setattr(oauth_app.httpx, "AsyncClient", lambda *a, **kw: _FakeClient())

    body = b"grant_type=authorization_code&code=xyz"
    with TestClient(oauth_app.app) as client:
        r = client.post(
            "/token",
            content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    assert r.status_code == 200
    assert r.json()["access_token"] == "from-cognito"
    assert captured["url"] == "https://fake-cognito.oauth.example/oauth2/token"
    assert captured["content"] == body
    assert captured["headers"].get("Content-Type") == "application/x-www-form-urlencoded"


def test_proxy_token_post_strips_resource_from_body(oauth_app, monkeypatch):
    captured: dict = {}

    class _FakeResp:
        def __init__(self) -> None:
            self.content = b'{"access_token":"ok"}'
            self.status_code = 200
            self.headers = {"content-type": "application/json"}

    class _FakeClient:
        def __init__(self, *_a, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def post(self, url, content=b"", headers=None):
            captured["content"] = content
            return _FakeResp()

    monkeypatch.setattr(oauth_app.httpx, "AsyncClient", lambda *a, **kw: _FakeClient())

    body = (
        b"grant_type=authorization_code&code=xyz"
        b"&resource=https%3A%2F%2Fmcp.example.com%2Fmcp"
    )
    with TestClient(oauth_app.app) as client:
        r = client.post(
            "/token",
            content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    assert r.status_code == 200
    decoded = captured["content"].decode()
    assert "grant_type=authorization_code" in decoded
    assert "code=xyz" in decoded
    assert "resource=" not in decoded


def test_proxy_token_get_converts_query_to_form_post(oauth_app, monkeypatch):
    captured: dict = {}

    class _FakeResp:
        def __init__(self) -> None:
            self.content = b'{"access_token":"get-via-query"}'
            self.status_code = 200
            self.headers = {"content-type": "application/json"}

    class _FakeClient:
        def __init__(self, *_a, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def post(self, url, content=b"", headers=None):
            captured["content"] = content
            captured["headers"] = dict(headers or {})
            return _FakeResp()

    monkeypatch.setattr(oauth_app.httpx, "AsyncClient", lambda *a, **kw: _FakeClient())

    with TestClient(oauth_app.app) as client:
        r = client.get(
            "/token",
            params={
                "grant_type": "authorization_code",
                "client_id": "x",
                "code": "abc",
                "resource": "https://mcp.example.com/mcp",
            },
        )
    assert r.status_code == 200
    assert r.json()["access_token"] == "get-via-query"
    assert captured["headers"].get("Content-Type") == "application/x-www-form-urlencoded"
    decoded = captured["content"].decode()
    assert "grant_type=authorization_code" in decoded
    assert "code=abc" in decoded
    assert "resource=" not in decoded


def test_mcp_requires_bearer(oauth_app):
    with TestClient(oauth_app.app) as client:
        r = client.post("/mcp", json={})
    assert r.status_code == 401
    www = r.headers.get("WWW-Authenticate", "")
    assert "Bearer" in www
    assert "resource_metadata=" in www
    assert "/.well-known/oauth-protected-resource/mcp" in www
    assert "as_uri=" not in www


def test_mcp_get_without_bearer_returns_200_for_claude_reachability(oauth_app):
    with TestClient(oauth_app.app) as client:
        r = client.get("/mcp")
    assert r.status_code == 200
    body = r.json()
    assert body.get("mcp_endpoint") is True
    assert body.get("authentication") == "oauth2"


def test_mcp_head_without_bearer_returns_200(oauth_app):
    with TestClient(oauth_app.app) as client:
        r = client.request("HEAD", "/mcp")
    assert r.status_code == 200


def test_mcp_get_with_bearer_still_validates(oauth_app, rsa_keypair):
    priv, _ = rsa_keypair
    token = _sign(priv, _good_claims())
    with TestClient(oauth_app.app) as client:
        r = client.get("/mcp", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code != 401


def test_post_mcp_401_includes_cors_expose_www_authenticate(oauth_app):
    with TestClient(oauth_app.app) as client:
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={"Origin": "https://claude.ai"},
        )
    assert r.status_code == 401
    exposed = (r.headers.get("access-control-expose-headers") or "").lower()
    assert "www-authenticate" in exposed
    assert "resource_metadata=" in (r.headers.get("WWW-Authenticate") or "")
    assert r.headers.get("access-control-allow-credentials") == "true"
    assert r.headers.get("access-control-allow-origin") == "https://claude.ai"


def test_options_mcp_cors_preflight_claude_origin(oauth_app):
    """Browser CORS preflight must not hit the OAuth gate (401). Stale ECS tasks
    without CORSMiddleware return 401 here — rebuild/push :latest and redeploy."""
    with TestClient(oauth_app.app) as client:
        r = client.options(
            "/mcp",
            headers={
                "Origin": "https://claude.ai",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type,mcp-session-id",
            },
        )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "https://claude.ai"
    allow_methods = r.headers.get("access-control-allow-methods") or ""
    assert "POST" in allow_methods


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
