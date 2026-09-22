"""Redirect policy and sealed state for the OAuth authorize broker."""

from __future__ import annotations

import urllib.parse

import pytest

from easydeploy_ai_mcp import oauth_broker


SECRET = b"test-broker-secret"


@pytest.mark.parametrize(
    "uri",
    [
        "https://claude.ai/api/mcp/auth_callback",
        "https://claude.com/api/mcp/auth_callback",
        # The per-connector callback that cannot be registered with Cognito
        # ahead of time — the reason the broker exists.
        "https://chatgpt.com/connector/oauth/abc123",
        "https://chatgpt.com/connector_platform_oauth_redirect",
        "https://chat.openai.com/aip/callback",
        "https://vscode.dev/redirect",
        "https://insiders.vscode.dev/redirect",
        # Runtime loopback ports, RFC 8252 §7.3.
        "http://127.0.0.1:51793/callback",
        "http://localhost:6274/oauth/callback",
        "http://[::1]:8123/cb",
        # Private-use scheme registered by a desktop client, RFC 8252 §7.1.
        "cursor://anysphere.cursor-retrieval/oauth/callback",
        "vscode://vscode.mcp/authenticate",
    ],
)
def test_allows_the_callbacks_real_clients_use(uri):
    assert oauth_broker.redirect_rejection(uri) == ""


@pytest.mark.parametrize(
    "uri",
    [
        "",
        "/relative/callback",
        # A lookalike registrable domain must not pass as a subdomain.
        "https://evilclaude.ai/cb",
        "https://claude.ai.attacker.example/cb",
        "https://attacker.example/cb",
        # Plain http off-loopback would put the code on the wire.
        "http://attacker.example/cb",
        # 0.0.0.0 is not a loopback address.
        "http://0.0.0.0:8080/cb",
        # Unregistered private-use scheme.
        "evilapp://callback",
        "javascript:alert(1)",
        # RFC 6749 §3.1.2 forbids a fragment on the redirect endpoint.
        "https://claude.ai/cb#frag",
    ],
)
def test_refuses_callbacks_that_would_leak_the_code(uri):
    assert oauth_broker.redirect_rejection(uri) != ""


def test_rejection_names_the_rule_that_was_tripped():
    reason = oauth_broker.redirect_rejection("https://attacker.example/cb")
    assert "attacker.example" in reason
    assert "redirect host" in reason


def test_subdomains_of_an_allowed_host_are_allowed():
    assert oauth_broker.is_allowed_redirect("https://api.claude.ai/cb")


def test_extra_hosts_come_from_the_environment(monkeypatch):
    assert not oauth_broker.is_allowed_redirect("https://partner.example/cb")
    monkeypatch.setenv("EDA_MCP_EXTRA_REDIRECT_HOSTS", "partner.example, other.example")
    assert oauth_broker.is_allowed_redirect("https://partner.example/cb")
    assert oauth_broker.is_allowed_redirect("https://deep.other.example/cb")


def test_extra_schemes_come_from_the_environment(monkeypatch):
    assert not oauth_broker.is_allowed_redirect("zed://callback")
    monkeypatch.setenv("EDA_MCP_EXTRA_REDIRECT_SCHEMES", "zed")
    assert oauth_broker.is_allowed_redirect("zed://callback")


def test_loopback_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("EDA_MCP_ALLOW_LOOPBACK_REDIRECT", "0")
    reason = oauth_broker.redirect_rejection("http://127.0.0.1:51793/callback")
    assert "disabled" in reason
    # HTTPS clients are unaffected by the loopback switch.
    assert oauth_broker.is_allowed_redirect("https://claude.ai/api/mcp/auth_callback")


def test_broker_is_off_unless_asked_for(monkeypatch):
    monkeypatch.delenv("EDA_MCP_OAUTH_BROKER", raising=False)
    assert oauth_broker.is_broker_enabled() is False
    monkeypatch.setenv("EDA_MCP_OAUTH_BROKER", "1")
    assert oauth_broker.is_broker_enabled() is True
    monkeypatch.setenv("EDA_MCP_OAUTH_BROKER", "0")
    assert oauth_broker.is_broker_enabled() is False


def test_seal_round_trips_the_client_redirect_and_state():
    sealed = oauth_broker.seal_state(
        "http://127.0.0.1:51793/callback", "client-state-xyz", SECRET
    )
    assert oauth_broker.unseal_state(sealed, SECRET) == (
        "http://127.0.0.1:51793/callback",
        "client-state-xyz",
    )


def test_seal_round_trips_when_the_client_sent_no_state():
    sealed = oauth_broker.seal_state("https://claude.ai/cb", None, SECRET)
    assert oauth_broker.unseal_state(sealed, SECRET) == ("https://claude.ai/cb", None)


def test_sealed_state_is_url_safe():
    sealed = oauth_broker.seal_state("https://claude.ai/cb", "a b/c+d", SECRET)
    assert urllib.parse.quote(sealed, safe="") == sealed


def test_tampered_state_does_not_unseal():
    sealed = oauth_broker.seal_state("https://claude.ai/cb", "s", SECRET)
    version, payload, signature = sealed.split(".")
    swapped = oauth_broker.seal_state("https://chatgpt.com/cb", "s", SECRET)
    # Somebody else's payload with this state's signature.
    forged = f"{version}.{swapped.split('.')[1]}.{signature}"
    assert oauth_broker.unseal_state(forged, SECRET) is None


@pytest.mark.parametrize(
    "state",
    ["", "not-a-state", "v1.onlytwo", "v2.abc.def", "v1..", "v1.###.###"],
)
def test_malformed_state_does_not_unseal(state):
    assert oauth_broker.unseal_state(state, SECRET) is None


def test_state_sealed_with_another_key_does_not_unseal():
    sealed = oauth_broker.seal_state("https://claude.ai/cb", "s", SECRET)
    assert oauth_broker.unseal_state(sealed, b"different-secret") is None


def test_unseal_rechecks_the_policy(monkeypatch):
    """A seal is not a grandfather clause: tightening the policy takes effect."""
    monkeypatch.setenv("EDA_MCP_EXTRA_REDIRECT_HOSTS", "partner.example")
    sealed = oauth_broker.seal_state("https://partner.example/cb", "s", SECRET)
    assert oauth_broker.unseal_state(sealed, SECRET) is not None
    monkeypatch.delenv("EDA_MCP_EXTRA_REDIRECT_HOSTS")
    assert oauth_broker.unseal_state(sealed, SECRET) is None


def test_secret_is_stable_across_processes_without_an_explicit_key(monkeypatch):
    monkeypatch.delenv("EDA_MCP_BROKER_SECRET", raising=False)
    first = oauth_broker.broker_secret(client_id="abc", user_pool_id="us-east-1_X")
    second = oauth_broker.broker_secret(client_id="abc", user_pool_id="us-east-1_X")
    assert first == second
    other = oauth_broker.broker_secret(client_id="abc", user_pool_id="us-east-1_Y")
    assert first != other


def test_explicit_secret_wins(monkeypatch):
    monkeypatch.setenv("EDA_MCP_BROKER_SECRET", "from-env")
    assert oauth_broker.broker_secret(client_id="abc", user_pool_id="p") == b"from-env"


def test_client_redirect_carries_the_code_and_the_client_state():
    target = oauth_broker.build_client_redirect(
        "http://127.0.0.1:51793/callback",
        "client-state",
        [("code", "auth-code-1"), ("state", "our-sealed-state")],
    )
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(target).query))
    assert query == {"code": "auth-code-1", "state": "client-state"}


def test_client_redirect_forwards_an_upstream_error():
    target = oauth_broker.build_client_redirect(
        "https://claude.ai/cb",
        "s",
        [("error", "access_denied"), ("error_description", "User cancelled")],
    )
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(target).query))
    assert query["error"] == "access_denied"
    assert query["error_description"] == "User cancelled"


def test_client_redirect_omits_state_when_the_client_sent_none():
    target = oauth_broker.build_client_redirect(
        "https://claude.ai/cb", None, [("code", "c"), ("state", "ours")]
    )
    assert "state" not in dict(
        urllib.parse.parse_qsl(urllib.parse.urlparse(target).query)
    )


def test_client_redirect_keeps_query_the_client_already_had():
    target = oauth_broker.build_client_redirect(
        "https://claude.ai/cb?tenant=acme", "s", [("code", "c")]
    )
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(target).query))
    assert query == {"tenant": "acme", "code": "c", "state": "s"}
