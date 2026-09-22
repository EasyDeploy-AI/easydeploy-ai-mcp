"""
oauth_broker.py — redirect policy and state sealing for the OAuth authorize broker.

Cognito matches ``redirect_uri`` **literally** against the app client's
callback list: no wildcards, no prefixes, no ports. It checks this at
``/oauth2/authorize``, before it renders the Hosted UI, so a client whose
callback is not on the list never sees a sign-in page at all — the browser
lands on ``redirect_mismatch``.

That is fine for clients with one fixed callback (Claude) and impossible for
the rest:

* ChatGPT falls back to ``https://chatgpt.com/connector/oauth/<callback_id>``,
  where the id is minted per connector.
* Desktop clients (Claude Code, Cursor, MCP Inspector) bind a loopback port at
  runtime — ``http://127.0.0.1:<whatever was free>/callback``.

Neither can be written into a static allowlist ahead of time. So the MCP
server brokers instead: it sends Cognito **its own** callback, which is one
fixed URL that can be registered once, and hands the authorization code back
to whatever the client actually asked for.

Doing that moves the redirect check from Cognito to us, and it has to stay a
real check. An open ``redirect_uri`` would let anyone start a flow at our
``/authorize`` with a callback they control, wait for a signed-in user to be
sent through it, and collect the authorization code — with a PKCE verifier
they chose themselves, so the code is redeemable. This module is that check:
a host allowlist for HTTPS, RFC 8252 loopback for native clients, and a
scheme allowlist for private-use URIs.

The state is sealed on top of that. Sealing is not what keeps the redirect
honest — an attacker can always ask ``/authorize`` to seal a state for them,
so forging one buys nothing the endpoint does not already offer. What it
buys is that a state in flight cannot be *edited*: the callback recovers the
same redirect URI the policy approved, rather than one a middlebox or a
crafted link swapped in. The callback re-runs the policy anyway.

Configuration:
    EDA_MCP_OAUTH_BROKER              ``1`` to broker (default off; see http_main)
    EDA_MCP_EXTRA_REDIRECT_HOSTS      comma-separated extra HTTPS hosts
    EDA_MCP_EXTRA_REDIRECT_SCHEMES    comma-separated extra private-use schemes
    EDA_MCP_ALLOW_LOOPBACK_REDIRECT   ``0`` to refuse loopback callbacks
    EDA_MCP_BROKER_SECRET             HMAC key for sealed state
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import urllib.parse

#: HTTPS callbacks are accepted on these hosts and their subdomains. Each is a
#: registrable domain owned by the client's vendor, which is as tight as this
#: can be while still covering per-connector paths we cannot enumerate.
DEFAULT_REDIRECT_HOSTS: tuple[str, ...] = (
    # Claude web, Claude Desktop.
    "claude.ai",
    "claude.com",
    # ChatGPT custom connectors: the documented callback and the per-connector
    # fallback (https://chatgpt.com/connector/oauth/<callback_id>).
    "chatgpt.com",
    "openai.com",
    # VS Code and Insiders bounce through their own redirect service.
    "vscode.dev",
    # Cursor's hosted callback.
    "cursor.com",
    "cursor.sh",
    # Our own console, for a first-party connect button.
    "easydeploy.ai",
)

#: Private-use URI schemes (RFC 8252 §7.1) registered by desktop clients.
DEFAULT_REDIRECT_SCHEMES: tuple[str, ...] = (
    "cursor",
    "vscode",
    "vscode-insiders",
    "windsurf",
    "code-oss",
)

#: RFC 8252 §7.3 loopback. Not ``0.0.0.0`` — that is not a loopback address.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

_STATE_VERSION = "v1"


def _env_list(name: str) -> list[str]:
    raw = os.environ.get(name, "").strip()
    return [item.strip() for item in raw.split(",") if item.strip()] if raw else []


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def is_broker_enabled() -> bool:
    """Brokering is opt-in.

    The broker's own callback has to be registered on the Cognito app client
    first. Turning this on before that deploy lands would break the clients
    that work today, so the default keeps the pass-through behaviour and the
    rollout is: register the callback, then set this.
    """
    return _env_flag("EDA_MCP_OAUTH_BROKER", False)


def allowed_redirect_hosts() -> tuple[str, ...]:
    return DEFAULT_REDIRECT_HOSTS + tuple(
        h.lower().lstrip(".") for h in _env_list("EDA_MCP_EXTRA_REDIRECT_HOSTS")
    )


def allowed_redirect_schemes() -> tuple[str, ...]:
    return DEFAULT_REDIRECT_SCHEMES + tuple(
        s.lower().rstrip(":") for s in _env_list("EDA_MCP_EXTRA_REDIRECT_SCHEMES")
    )


def loopback_allowed() -> bool:
    """Loopback is how every native client does OAuth; off is available, not advised."""
    return _env_flag("EDA_MCP_ALLOW_LOOPBACK_REDIRECT", True)


def _host_matches(host: str, allowed: str) -> bool:
    """``allowed`` covers itself and its subdomains, never a suffix match.

    ``evilclaude.ai`` must not pass for ``claude.ai``, so the subdomain arm
    tests against ``"." + allowed`` rather than the bare string.
    """
    return host == allowed or host.endswith("." + allowed)


def _is_loopback_host(host: str) -> bool:
    if host in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def redirect_rejection(uri: str) -> str:
    """Why ``uri`` may not be used as a client callback; empty string if it may.

    Returns the reason rather than a bool so the 400 can say which rule the
    client tripped — a connector author staring at "invalid redirect_uri" has
    nothing to act on.
    """
    if not uri:
        return "redirect_uri is required"
    try:
        parsed = urllib.parse.urlparse(uri)
    except ValueError:
        return "redirect_uri is not a valid URI"

    scheme = (parsed.scheme or "").lower()
    if not scheme:
        return "redirect_uri must be absolute"
    if parsed.fragment:
        # RFC 6749 §3.1.2: the endpoint URI must not include a fragment.
        return "redirect_uri must not contain a fragment"

    host = (parsed.hostname or "").lower().strip("[]")

    if scheme == "http":
        if not _is_loopback_host(host):
            return "http redirect_uri is only accepted on a loopback address"
        if not loopback_allowed():
            return "loopback redirect_uri is disabled on this deployment"
        return ""

    if scheme == "https":
        if not host:
            return "https redirect_uri must have a host"
        if any(_host_matches(host, allowed) for allowed in allowed_redirect_hosts()):
            return ""
        return f"host {host!r} is not an allowed redirect host"

    if scheme in allowed_redirect_schemes():
        return ""

    return f"scheme {scheme!r} is not an allowed redirect scheme"


def is_allowed_redirect(uri: str) -> bool:
    return redirect_rejection(uri) == ""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def broker_secret(*, client_id: str = "", user_pool_id: str = "") -> bytes:
    """HMAC key for sealed state.

    ``EDA_MCP_BROKER_SECRET`` when set. Otherwise derived from the Cognito
    identifiers, which are already in the task environment and are identical
    on every task in a stage — a random per-process key would invalidate any
    login that straddled a deploy or a second replica.
    """
    explicit = os.environ.get("EDA_MCP_BROKER_SECRET", "").strip()
    if explicit:
        return explicit.encode("utf-8")
    seed = f"easydeploy-mcp-broker:{user_pool_id}:{client_id}"
    return hashlib.sha256(seed.encode("utf-8")).digest()


def _sign(payload: str, secret: bytes) -> str:
    return _b64url(hmac.new(secret, payload.encode("ascii"), hashlib.sha256).digest())


def seal_state(client_redirect_uri: str, client_state: str | None, secret: bytes) -> str:
    """Pack the client's callback and state into one opaque, tamper-evident value.

    No expiry: the authorization code Cognito issues against it expires on its
    own in minutes, and a replayed state with no code attached only starts a
    fresh sign-in.
    """
    body: dict[str, str] = {"u": client_redirect_uri}
    if client_state:
        body["s"] = client_state
    payload = _b64url(json.dumps(body, separators=(",", ":")).encode("utf-8"))
    return f"{_STATE_VERSION}.{payload}.{_sign(payload, secret)}"


def unseal_state(state: str, secret: bytes) -> tuple[str, str | None] | None:
    """``(client_redirect_uri, client_state)``, or ``None`` if it did not come from us.

    The redirect is re-checked against the policy here as well as at
    ``/authorize``: the seal proves the value is unmodified, not that the
    policy that approved it still holds.
    """
    if not state:
        return None
    parts = state.split(".")
    if len(parts) != 3 or parts[0] != _STATE_VERSION:
        return None
    _, payload, signature = parts
    if not hmac.compare_digest(signature, _sign(payload, secret)):
        return None
    try:
        body = json.loads(_b64url_decode(payload).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(body, dict):
        return None
    redirect_uri = body.get("u")
    if not isinstance(redirect_uri, str) or not is_allowed_redirect(redirect_uri):
        return None
    client_state = body.get("s")
    return redirect_uri, client_state if isinstance(client_state, str) else None


def build_client_redirect(
    client_redirect_uri: str,
    client_state: str | None,
    upstream_params: list[tuple[str, str]],
) -> str:
    """The URL to send the browser to once Cognito comes back.

    Everything Cognito returned is forwarded except its ``state`` — which was
    ours, not the client's — so an ``error``/``error_description`` reaches the
    client the same way a ``code`` does. A client that sent no state gets none
    back, as RFC 6749 §4.1.2 requires.
    """
    passthrough = [(k, v) for k, v in upstream_params if k != "state"]
    if client_state is not None:
        passthrough.append(("state", client_state))
    if not passthrough:
        return client_redirect_uri
    parsed = urllib.parse.urlparse(client_redirect_uri)
    merged = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True) + passthrough
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(merged)))
