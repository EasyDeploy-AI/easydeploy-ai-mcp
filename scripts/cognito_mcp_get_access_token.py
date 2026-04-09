#!/usr/bin/env python3
"""
Interactive PKCE login against Cognito Hosted UI for the MCP app client
(same idea as accessible-ai/scripts/cognito_mcp_get_access_token.py; this copy
lives in easydeploy-ai-mcp so you can run it from this repo).

Prints a shell-ready export for EDA_SMOKE_ACCESS_TOKEN.

--cognito-host is your Cognito domain, e.g.:
  myprefix.auth.us-east-1.amazoncognito.com
  or a custom domain: auth.sandbox.easydeploy.ai

It is NOT the API Gateway URL (execute-api.amazonaws.com).

Env fallbacks: COGNITO_HOSTED_UI_HOST, EDA_COGNITO_CLIENT_ID (optional --flags).

Redirect URI default: http://localhost:6274/oauth/callback (must match Cognito allowlist).

SSL: token exchange uses HTTPS from Python. On macOS, python.org builds often lack
system CA bundle — run "Install Certificates.command" from the Python folder, or
`pip install certifi` (script uses certifi's bundle when available), or pass
`--insecure-ssl` for local dev only (disables verification for the token POST).

Custom domain "Login pages unavailable" is a Cognito/AWS configuration issue
(domain not active, Hosted UI disabled, or wrong pool) — not fixed by this script.
Use the default *.amazoncognito.com domain for PKCE if the custom domain fails.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import ssl
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer


def _normalize_host(raw: str) -> str:
    s = raw.strip()
    if s.startswith("https://"):
        s = s[len("https://") :]
    if s.startswith("http://"):
        s = s[len("http://") :]
    return s.split("/")[0].strip()


def _reject_api_gateway_host(host: str) -> None:
    h = host.lower()
    if "execute-api" in h and "amazonaws.com" in h:
        print(
            "error: this host looks like API Gateway, not Cognito.\n"
            "Use your Cognito domain (e.g. *.auth.*.amazoncognito.com or a custom auth host).",
            file=sys.stderr,
        )
        sys.exit(1)


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def _https_opener(insecure_ssl: bool) -> urllib.request.OpenerDirector:
    if insecure_ssl:
        print(
            "warning: --insecure-ssl: TLS verification disabled for POST /oauth2/token only.",
            file=sys.stderr,
        )
        ctx = ssl._create_unverified_context()
    else:
        try:
            import certifi

            ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            ctx = ssl.create_default_context()
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))


def _urlopen_token(req: urllib.request.Request, *, insecure_ssl: bool, timeout: int):
    opener = _https_opener(insecure_ssl)

    def _ssl_hint() -> None:
        print(
            "\nSSL verify failed on token POST. Fixes (pick one):\n"
            "  - macOS python.org: run Applications/Python 3.x/Install Certificates.command\n"
            "  - pip install certifi (this script will use it automatically)\n"
            "  - Dev only: rerun with --insecure-ssl\n",
            file=sys.stderr,
        )

    try:
        return opener.open(req, timeout=timeout)
    except ssl.SSLError as e:
        if "CERTIFICATE_VERIFY_FAILED" in str(e) or isinstance(e, ssl.SSLCertVerificationError):
            _ssl_hint()
        raise
    except urllib.error.URLError as e:
        r = getattr(e, "reason", None)
        if isinstance(r, ssl.SSLError) and (
            "CERTIFICATE_VERIFY_FAILED" in str(r) or isinstance(r, ssl.SSLCertVerificationError)
        ):
            _ssl_hint()
        elif "CERTIFICATE_VERIFY_FAILED" in str(e):
            _ssl_hint()
        raise


def _parse_bind_host_port(redirect_uri: str) -> tuple[str, int]:
    p = urllib.parse.urlparse(redirect_uri)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise SystemExit(f"Invalid --redirect-uri: {redirect_uri!r}")
    if p.scheme != "http":
        raise SystemExit("Local callback must use http:// (use http://localhost:…).")
    host = "127.0.0.1" if p.hostname in ("localhost", "127.0.0.1") else p.hostname
    port = p.port or 80
    return host, port


def main() -> None:
    env_host = os.environ.get("COGNITO_HOSTED_UI_HOST", "").strip()
    env_client = os.environ.get("EDA_COGNITO_CLIENT_ID", "").strip()

    parser = argparse.ArgumentParser(
        description="Get a Cognito access token (PKCE) for MCP smoke tests.",
    )
    parser.add_argument(
        "--cognito-host",
        default=env_host or None,
        required=not env_host,
        nargs="?" if env_host else None,
        metavar="HOST",
        help="Cognito domain host (https optional). Or set COGNITO_HOSTED_UI_HOST.",
    )
    parser.add_argument(
        "--client-id",
        default=env_client or None,
        required=not env_client,
        nargs="?" if env_client else None,
        metavar="ID",
        help="MCP app client id. Or set EDA_COGNITO_CLIENT_ID.",
    )
    parser.add_argument(
        "--redirect-uri",
        default="http://localhost:6274/oauth/callback",
        help="Must match Cognito app client callback allowlist",
    )
    parser.add_argument(
        "--scope",
        default="openid email profile",
    )
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument(
        "--authorize-path",
        default="oauth2/authorize",
        help="Path under cognito host (default: oauth2/authorize). Rarely 'login' for some custom setups.",
    )
    parser.add_argument(
        "--insecure-ssl",
        action="store_true",
        help="Disable TLS certificate verification for POST /oauth2/token only (dev only)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Print authorize URL only",
    )
    args = parser.parse_args()

    ch = (args.cognito_host or env_host or "").strip()
    cid = (args.client_id or env_client or "").strip()
    if not ch or not cid:
        print(
            "Need --cognito-host and --client-id (or COGNITO_HOSTED_UI_HOST and EDA_COGNITO_CLIENT_ID).",
            file=sys.stderr,
        )
        sys.exit(1)

    cognito_host = _normalize_host(ch)
    _reject_api_gateway_host(cognito_host)

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    bind_host, bind_port = _parse_bind_host_port(args.redirect_uri)

    result: dict[str, str | None] = {"code": None, "error": None}
    done = threading.Event()
    redirect_path = urllib.parse.urlparse(args.redirect_uri).path or "/"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *log_args: object) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != redirect_path:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not found")
                return
            q = urllib.parse.parse_qs(parsed.query)
            if q.get("error"):
                result["error"] = q.get("error_description", q["error"])[0]
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Authorization failed - see terminal.")
                done.set()
                return
            code_list = q.get("code")
            st_list = q.get("state")
            if not code_list or not st_list:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing code or state")
                done.set()
                return
            if st_list[0] != state:
                result["error"] = "state mismatch"
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Invalid state")
                done.set()
                return
            result["code"] = code_list[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><p>OK - you can close this tab.</p></body></html>"
            )
            done.set()

    server = HTTPServer((bind_host, bind_port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    auth_params = {
        "response_type": "code",
        "client_id": cid,
        "redirect_uri": args.redirect_uri,
        "scope": args.scope,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    auth_path = args.authorize_path.strip().strip("/")
    auth_url = (
        f"https://{cognito_host}/{auth_path}?"
        + urllib.parse.urlencode(auth_params, quote_via=urllib.parse.quote)
    )

    print("Open this URL in your browser (or wait if it opened automatically):\n")
    print(auth_url)
    print()
    if not args.no_browser:
        webbrowser.open(auth_url)

    if not done.wait(timeout=args.timeout):
        print("Timed out waiting for redirect.", file=sys.stderr)
        server.shutdown()
        sys.exit(1)
    server.shutdown()

    if result["error"]:
        print(f"OAuth error: {result['error']}", file=sys.stderr)
        sys.exit(1)
    code = result["code"]
    assert code is not None

    token_url = f"https://{cognito_host}/oauth2/token"
    body = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": cid,
            "code": code,
            "redirect_uri": args.redirect_uri,
            "code_verifier": verifier,
        }
    ).encode("ascii")
    req = urllib.request.Request(
        token_url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with _urlopen_token(req, insecure_ssl=args.insecure_ssl, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        print(f"Token exchange failed: HTTP {e.code}\n{err_body}", file=sys.stderr)
        sys.exit(1)

    access = payload.get("access_token")
    if not access:
        print(json.dumps(payload, indent=2), file=sys.stderr)
        print("No access_token in response.", file=sys.stderr)
        sys.exit(1)

    print("\n# Paste into your shell, then run validate_mcp_sandbox.sh:\n")
    print(f'export EDA_SMOKE_ACCESS_TOKEN="{access}"')
    print()


if __name__ == "__main__":
    main()
