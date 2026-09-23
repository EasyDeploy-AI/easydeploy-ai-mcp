"""ASGI app: healthz and optional MCP_SERVICE_TOKEN gate."""

from __future__ import annotations

import importlib
import urllib.parse

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
        pre = client.options(
            "/mcp",
            headers={
                "Origin": "https://claude.ai",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert pre.status_code == 200
        assert pre.headers.get("access-control-allow-origin") == "https://claude.ai"

    monkeypatch.delenv("MCP_SERVICE_TOKEN", raising=False)
    importlib.reload(http_main)


# --- OAuth authorize broker -------------------------------------------------
#
# The broker exists because Cognito matches ``redirect_uri`` literally: clients
# with a runtime loopback port or a per-connector callback can never be
# registered, so the MCP server sends Cognito its own callback instead and
# forwards the code on. These tests drive the whole hop.

ISSUER = "https://mcp.example.test"
BROKER_CALLBACK = f"{ISSUER}/oauth/callback"
COGNITO_AUTHORIZE = "https://auth.example.test/oauth2/authorize"
COGNITO_TOKEN = "https://auth.example.test/oauth2/token"


@pytest.fixture
def oauth_app(monkeypatch, request):
    """Reload the ASGI app in OAuth mode, brokering unless the test says otherwise."""
    import easydeploy_ai_mcp.http_main as http_main
    import easydeploy_ai_mcp.oauth_as_metadata as oauth_as_metadata

    broker = getattr(request, "param", True)
    monkeypatch.delenv("MCP_SERVICE_TOKEN", raising=False)
    monkeypatch.setenv("EDA_OAUTH_ENABLED", "1")
    monkeypatch.setenv("EDA_COGNITO_USER_POOL_ID", "us-east-1_Test")
    monkeypatch.setenv("EDA_COGNITO_CLIENT_ID", "testclientid")
    monkeypatch.setenv("EDA_COGNITO_REGION", "us-east-1")
    monkeypatch.setenv("EDA_MCP_OAUTH_ISSUER", ISSUER)
    monkeypatch.setenv("EDA_MCP_BROKER_SECRET", "unit-test-secret")
    monkeypatch.setenv("EDA_MCP_OAUTH_BROKER", "1" if broker else "0")
    monkeypatch.setattr(
        oauth_as_metadata,
        "fetch_cognito_openid_configuration_json",
        lambda _issuer: {
            "authorization_endpoint": COGNITO_AUTHORIZE,
            "token_endpoint": COGNITO_TOKEN,
        },
    )
    importlib.reload(http_main)
    try:
        yield http_main
    finally:
        for name in (
            "EDA_OAUTH_ENABLED",
            "EDA_COGNITO_USER_POOL_ID",
            "EDA_COGNITO_CLIENT_ID",
            "EDA_COGNITO_REGION",
            "EDA_MCP_OAUTH_ISSUER",
            "EDA_MCP_BROKER_SECRET",
            "EDA_MCP_OAUTH_BROKER",
        ):
            monkeypatch.delenv(name, raising=False)
        importlib.reload(http_main)


def _authorize_query(client, redirect_uri, state="client-state"):
    """Follow /authorize one hop and return the query Cognito would have seen."""
    r = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": "testclientid",
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": "chal",
            "code_challenge_method": "S256",
            "scope": "openid email profile",
        },
        follow_redirects=False,
    )
    return r


@pytest.mark.parametrize("oauth_app", [False], indirect=True)
def test_authorize_passes_the_redirect_through_when_not_brokering(oauth_app):
    with TestClient(oauth_app.app) as client:
        r = _authorize_query(client, "https://claude.ai/api/mcp/auth_callback")
    assert r.status_code == 302
    location = urllib.parse.urlparse(r.headers["location"])
    query = dict(urllib.parse.parse_qsl(location.query))
    assert query["redirect_uri"] == "https://claude.ai/api/mcp/auth_callback"
    assert query["state"] == "client-state"


@pytest.mark.parametrize("oauth_app", [False], indirect=True)
def test_broker_callback_is_absent_when_not_brokering(oauth_app):
    with TestClient(oauth_app.app) as client:
        r = client.get("/oauth/callback", params={"code": "c"}, follow_redirects=False)
    # Falls through to the MCP mount rather than answering as a broker.
    assert r.status_code != 302


@pytest.mark.parametrize(
    "client_redirect",
    [
        "https://chatgpt.com/connector/oauth/abc123",
        "http://127.0.0.1:51793/callback",
        "cursor://anysphere.cursor-retrieval/oauth/callback",
    ],
)
def test_broker_substitutes_its_own_redirect_for_any_allowed_client(
    oauth_app, client_redirect
):
    with TestClient(oauth_app.app) as client:
        r = _authorize_query(client, client_redirect)
    assert r.status_code == 302
    location = urllib.parse.urlparse(r.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == COGNITO_AUTHORIZE
    query = dict(urllib.parse.parse_qsl(location.query))
    assert query["redirect_uri"] == BROKER_CALLBACK
    # PKCE and scope reach Cognito untouched.
    assert query["code_challenge"] == "chal"
    assert query["code_challenge_method"] == "S256"
    assert query["scope"] == "openid email profile"
    # The client's own state is sealed inside ours, never sent as-is.
    assert query["state"] != "client-state"


def test_broker_refuses_a_callback_it_does_not_trust(oauth_app):
    with TestClient(oauth_app.app) as client:
        r = _authorize_query(client, "https://attacker.example/cb")
    # Answered here, not redirected to — a 302 to the URI under suspicion is
    # exactly the open redirect the policy is there to prevent.
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_request"
    assert "attacker.example" in r.json()["error_description"]
    assert "location" not in r.headers


@pytest.mark.parametrize(
    "pkce",
    [
        {},
        {"code_challenge": "chal"},
        {"code_challenge": "chal", "code_challenge_method": "plain"},
    ],
    ids=["none", "no-method", "plain"],
)
def test_broker_refuses_a_flow_without_s256_pkce(oauth_app, pkce):
    """Cognito treats PKCE as optional, and /token no longer checks the
    client's redirect_uri in broker mode, so without PKCE a leaked code would
    be redeemable by anyone. Refused here, before Cognito sees it."""
    with TestClient(oauth_app.app) as client:
        r = client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": "testclientid",
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "state": "client-state",
                **pkce,
            },
            follow_redirects=False,
        )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_request"
    assert "code_challenge" in r.json()["error_description"]
    assert "location" not in r.headers


@pytest.mark.parametrize("oauth_app", [False], indirect=True)
def test_pkce_is_cognitos_business_when_not_brokering(oauth_app):
    """Pass-through mode leaves Cognito's own redirect_uri check in place."""
    with TestClient(oauth_app.app) as client:
        r = client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": "testclientid",
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "state": "client-state",
            },
            follow_redirects=False,
        )
    assert r.status_code == 302


def test_broker_hands_the_code_back_to_the_client(oauth_app):
    with TestClient(oauth_app.app) as client:
        authorized = _authorize_query(client, "http://127.0.0.1:51793/callback")
        sealed = dict(
            urllib.parse.parse_qsl(
                urllib.parse.urlparse(authorized.headers["location"]).query
            )
        )["state"]
        r = client.get(
            "/oauth/callback",
            params={"code": "auth-code-1", "state": sealed},
            follow_redirects=False,
        )
    assert r.status_code == 302
    target = urllib.parse.urlparse(r.headers["location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == (
        "http://127.0.0.1:51793/callback"
    )
    assert dict(urllib.parse.parse_qsl(target.query)) == {
        "code": "auth-code-1",
        "state": "client-state",
    }


def test_broker_forwards_a_sign_in_error_to_the_client(oauth_app):
    with TestClient(oauth_app.app) as client:
        authorized = _authorize_query(client, "https://claude.ai/api/mcp/auth_callback")
        sealed = dict(
            urllib.parse.parse_qsl(
                urllib.parse.urlparse(authorized.headers["location"]).query
            )
        )["state"]
        r = client.get(
            "/oauth/callback",
            params={"error": "access_denied", "state": sealed},
            follow_redirects=False,
        )
    assert r.status_code == 302
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(r.headers["location"]).query))
    assert query["error"] == "access_denied"
    assert query["state"] == "client-state"


def test_broker_callback_refuses_a_state_it_did_not_issue(oauth_app):
    with TestClient(oauth_app.app) as client:
        r = client.get(
            "/oauth/callback",
            params={"code": "c", "state": "v1.forged.forged"},
            follow_redirects=False,
        )
    assert r.status_code == 400
    assert "location" not in r.headers


def test_broker_callback_needs_no_bearer(oauth_app):
    """Cognito arrives with a browser and no token; a 401 here would end the flow."""
    with TestClient(oauth_app.app) as client:
        r = client.get("/oauth/callback", follow_redirects=False)
    assert r.status_code == 400  # rejected on state, not on auth


@pytest.fixture
def captured_token_request(monkeypatch):
    """Intercept the outbound token POST so the forwarded form can be inspected."""
    import httpx

    captured: dict = {}

    async def fake_post(_self, url, *, content=None, headers=None, **_kwargs):
        captured["url"] = url
        captured["form"] = dict(
            urllib.parse.parse_qsl((content or b"").decode(), keep_blank_values=True)
        )
        captured["headers"] = headers or {}
        return httpx.Response(
            200,
            json={"access_token": "tok", "token_type": "Bearer"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    return captured


def test_token_exchange_uses_the_brokers_redirect_not_the_clients(
    oauth_app, captured_token_request
):
    """Cognito checks ``redirect_uri`` again at /token against the one it issued
    the code for — which was ours. Sending the client's would fail the exchange
    *after* a successful sign-in."""
    with TestClient(oauth_app.app) as client:
        r = client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": "auth-code-1",
                "redirect_uri": "http://127.0.0.1:51793/callback",
                "code_verifier": "verifier",
                "client_id": "testclientid",
            },
        )
    assert r.status_code == 200
    assert captured_token_request["url"] == COGNITO_TOKEN
    form = captured_token_request["form"]
    assert form["redirect_uri"] == BROKER_CALLBACK
    # Everything the client actually needs to prove is untouched.
    assert form["code"] == "auth-code-1"
    assert form["code_verifier"] == "verifier"


def test_token_exchange_rewrites_the_redirect_on_a_get_too(
    oauth_app, captured_token_request
):
    """Some Claude.ai builds issue GET /token with query params (claude-ai-mcp#82)."""
    with TestClient(oauth_app.app) as client:
        r = client.get(
            "/token",
            params={
                "grant_type": "authorization_code",
                "code": "c",
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
            },
        )
    assert r.status_code == 200
    assert captured_token_request["form"]["redirect_uri"] == BROKER_CALLBACK


def test_refresh_exchange_is_left_alone(oauth_app, captured_token_request):
    with TestClient(oauth_app.app) as client:
        client.post(
            "/token", data={"grant_type": "refresh_token", "refresh_token": "r"}
        )
    assert "redirect_uri" not in captured_token_request["form"]


@pytest.mark.parametrize("oauth_app", [False], indirect=True)
def test_token_exchange_keeps_the_client_redirect_when_not_brokering(
    oauth_app, captured_token_request
):
    with TestClient(oauth_app.app) as client:
        client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": "c",
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
            },
        )
    assert captured_token_request["form"]["redirect_uri"] == (
        "https://claude.ai/api/mcp/auth_callback"
    )


# --- CORS -------------------------------------------------------------------
#
# A browser-hosted client whose origin is missing from the allowlist never gets
# to send the request: the preflight fails and the assistant reports that it
# could not reach the server, with nothing on our side to show for it.

@pytest.mark.parametrize(
    "origin",
    [
        "https://claude.ai",
        "https://claude.com",
        "https://chatgpt.com",
        "https://chat.openai.com",
        "https://vscode.dev",
        "https://insiders.vscode.dev",
        "https://cursor.com",
        "http://localhost:6274",
    ],
)
def test_preflight_is_allowed_for_every_documented_browser_client(oauth_app, origin):
    with TestClient(oauth_app.app) as client:
        r = client.options(
            "/mcp",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == origin


def test_the_oauth_challenge_is_readable_from_the_browser(oauth_app):
    """Browsers hide WWW-Authenticate from fetch() unless it is exposed on the
    actual response — and without reading it a client cannot find the resource
    metadata, so the 401 that should start OAuth reads as an opaque failure."""
    with TestClient(oauth_app.app) as client:
        r = client.post("/mcp", json={}, headers={"Origin": "https://chatgpt.com"})
    assert r.status_code == 401
    assert r.headers.get("access-control-allow-origin") == "https://chatgpt.com"
    assert "WWW-Authenticate" in r.headers.get("access-control-expose-headers", "")
    assert "resource_metadata=" in r.headers["www-authenticate"]


def test_preflight_is_refused_for_an_unknown_origin(oauth_app):
    with TestClient(oauth_app.app) as client:
        r = client.options(
            "/mcp",
            headers={
                "Origin": "https://attacker.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization",
            },
        )
    assert r.headers.get("access-control-allow-origin") is None


def test_extra_cors_origins_come_from_the_environment(monkeypatch):
    import easydeploy_ai_mcp.http_main as http_main

    monkeypatch.setenv("EDA_CORS_EXTRA_ORIGINS", "https://partner.example")
    importlib.reload(http_main)
    try:
        with TestClient(http_main.app) as client:
            r = client.options(
                "/mcp",
                headers={
                    "Origin": "https://partner.example",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "authorization",
                },
            )
        assert r.headers.get("access-control-allow-origin") == "https://partner.example"
    finally:
        monkeypatch.delenv("EDA_CORS_EXTRA_ORIGINS", raising=False)
        importlib.reload(http_main)


# --- end to end -------------------------------------------------------------


class _FakeCognito:
    """Cognito's two checks, which is all that matters here.

    ``/oauth2/authorize`` compares ``redirect_uri`` against the app client's
    callback list **literally**, before it renders a sign-in page. ``/oauth2/token``
    then compares it against the value the code was issued for. A client whose
    callback is not registered fails the first check, and one that changes the
    value between the two fails the second.
    """

    REGISTERED = (BROKER_CALLBACK,)

    def __init__(self):
        self.codes: dict[str, str] = {}

    def authorize(self, query: dict[str, str]) -> tuple[str, str]:
        redirect = query.get("redirect_uri", "")
        if redirect not in self.REGISTERED:
            return "redirect_mismatch", ""
        self.codes["granted-code"] = redirect
        return "", (
            f"{redirect}?code=granted-code&state={urllib.parse.quote(query.get('state', ''))}"
        )

    def token(self, form: dict[str, str]) -> tuple[str, str]:
        issued_for = self.codes.get(form.get("code", ""))
        if issued_for is None:
            return "invalid_grant", ""
        if form.get("redirect_uri") != issued_for:
            return "redirect_mismatch", ""
        return "", "access-token"


def _run_full_flow(app, client_redirect, cognito):
    """Drive authorize → Cognito → callback → token; return (error, final url, token)."""
    import httpx

    with TestClient(app) as client:
        authorized = client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": "testclientid",
                "redirect_uri": client_redirect,
                "state": "client-state",
                "code_challenge": "chal",
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )
        if authorized.status_code != 302:
            return "rejected_by_broker", "", ""

        upstream = urllib.parse.urlparse(authorized.headers["location"])
        error, landing = cognito.authorize(dict(urllib.parse.parse_qsl(upstream.query)))
        if error:
            return error, "", ""

        landed = urllib.parse.urlparse(landing)
        final = landing
        if f"{landed.scheme}://{landed.netloc}{landed.path}" == BROKER_CALLBACK:
            forwarded = client.get(
                landed.path,
                params=dict(urllib.parse.parse_qsl(landed.query)),
                follow_redirects=False,
            )
            if forwarded.status_code != 302:
                return "rejected_by_callback", "", ""
            final = forwarded.headers["location"]

        captured: dict = {}

        async def fake_post(_self, url, *, content=None, headers=None, **_kwargs):
            captured["form"] = dict(
                urllib.parse.parse_qsl((content or b"").decode(), keep_blank_values=True)
            )
            err, token = cognito.token(captured["form"])
            body = {"error": err} if err else {"access_token": token}
            return httpx.Response(
                400 if err else 200, json=body, request=httpx.Request("POST", url)
            )

        original = httpx.AsyncClient.post
        httpx.AsyncClient.post = fake_post
        try:
            code = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(final).query))["code"]
            exchanged = client.post(
                "/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": client_redirect,
                    "code_verifier": "verifier",
                },
            ).json()
        finally:
            httpx.AsyncClient.post = original

    return exchanged.get("error", ""), final, exchanged.get("access_token", "")


@pytest.mark.parametrize(
    "client_redirect",
    [
        "https://claude.ai/api/mcp/auth_callback",
        "https://chatgpt.com/connector/oauth/abc123",
        "http://127.0.0.1:51793/callback",
    ],
)
def test_broker_gets_a_token_for_a_client_cognito_would_have_turned_away(
    oauth_app, client_redirect
):
    error, final, token = _run_full_flow(oauth_app.app, client_redirect, _FakeCognito())
    assert error == ""
    assert token == "access-token"
    parsed = urllib.parse.urlparse(final)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == client_redirect
    assert dict(urllib.parse.parse_qsl(parsed.query))["state"] == "client-state"


@pytest.mark.parametrize("oauth_app", [False], indirect=True)
@pytest.mark.parametrize(
    "client_redirect",
    [
        "https://chatgpt.com/connector/oauth/abc123",
        "http://127.0.0.1:51793/callback",
    ],
)
def test_without_the_broker_the_same_clients_never_reach_a_sign_in_page(
    oauth_app, client_redirect
):
    """The symptom this change fixes: ``redirect_mismatch`` at authorize, so the
    connector reports that it cannot load the sign-in page."""
    error, _, _ = _run_full_flow(oauth_app.app, client_redirect, _FakeCognito())
    assert error == "redirect_mismatch"
