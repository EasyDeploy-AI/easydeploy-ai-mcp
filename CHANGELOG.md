# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- **Brokered OAuth requires PKCE.** In broker mode `/authorize` refuses a request without `code_challenge_method=S256`. Cognito treats PKCE as optional and the broker's `/token` rewrite removes Cognito's own `redirect_uri` check, so a flow without it would leave a leaked code redeemable by anyone.
- **Broker redirect policy matches hosts exactly and pins known callback paths.** Subdomains of vendor hosts are no longer implied (`EDA_MCP_EXTRA_REDIRECT_HOSTS` takes `.host` to opt in), `openai.com` is narrowed to `chat.openai.com/aip/`, and URIs with dot segments, backslashes, whitespace or non-ASCII are refused so the policy and the browser cannot disagree about where a redirect goes.
- The server logs a warning when brokering without `EDA_MCP_BROKER_SECRET`; the fallback key is derived from public identifiers.

### Added

- **OAuth 2.0 resource-server mode** for the HTTP transport. Set `EDA_OAUTH_ENABLED=1` with `EDA_COGNITO_USER_POOL_ID` and `EDA_COGNITO_CLIENT_ID` to validate incoming `Authorization: Bearer <jwt>` headers locally against the Cognito JWKS and forward the user's token to the EasyDeploy API. EasyDeploy API keys (`eda_live_*`) are accepted in the same header and forwarded verbatim.
- **`/.well-known/oauth-protected-resource`** (RFC 9728) published in OAuth mode so MCP clients can discover the authorization server from a 401. Unauthorized responses include a `WWW-Authenticate: Bearer …` header.
- **OAuth AS metadata proxy** (`/.well-known/oauth-authorization-server`) at the MCP origin. Proxies `authorization_endpoint` and `token_endpoint` from Cognito's OIDC discovery, stripping RFC 8707 `resource` params that Cognito rejects.
- **Static DCR** (`POST /oauth/register`, `POST /register`) returns the pre-configured Cognito public client id (RFC 7591-style).
- **`/authorize`** and **`/token`** proxy endpoints for MCP OAuth broker compatibility.
- **`scripts/run_mcp_docker_local.sh`** — build the Dockerfile and run the HTTP MCP locally.
- **Brokered authorization** (`EDA_MCP_OAUTH_BROKER=1`, default off). `/authorize` sends Cognito the server's own `{issuer}/oauth/callback` and seals the client's `redirect_uri` into the OAuth `state`; the new `GET /oauth/callback` forwards the authorization code to that URI with the client's own `state` restored, and `/token` rewrites `redirect_uri` to match. This is what lets clients Cognito cannot allowlist sign in at all — ChatGPT's per-connector `https://chatgpt.com/connector/oauth/<callback_id>`, and the runtime loopback ports Claude Code, Cursor and MCP Inspector bind. Register `{issuer}/oauth/callback` on the Cognito app client before turning it on. See [Brokered authorization](README.md#brokered-authorization).
- **`oauth_broker` redirect policy** — brokering moves the `redirect_uri` check off Cognito, so the server enforces its own: HTTPS on a known vendor host or subdomain, RFC 8252 §7.3 loopback, and a short private-use scheme allowlist. Extend with `EDA_MCP_EXTRA_REDIRECT_HOSTS`, `EDA_MCP_EXTRA_REDIRECT_SCHEMES`, `EDA_MCP_ALLOW_LOOPBACK_REDIRECT`; seal the state with `EDA_MCP_BROKER_SECRET`.

### Fixed

- **Browser CORS beyond Claude:** the allowlist now covers ChatGPT (`chatgpt.com`, `chat.openai.com`), VS Code Web and Insiders, Cursor, and a locally run MCP Inspector, alongside the Claude origins. A browser-hosted client whose origin was missing never got to send the request — the preflight failed and the assistant reported that it could not reach the server. Add more with `EDA_CORS_EXTRA_ORIGINS`.
- **MCP OAuth discovery:** RFC 9728 resource metadata lists the MCP host in `authorization_servers`; the AS metadata proxy ensures every client hits the local endpoints rather than Cognito directly (Cognito's pool issuer does not serve `oauth-authorization-server`).
- **`api_client`:** REST helpers now consistently accept and forward `caller_channel` to `X-Caller-Channel`.

### Changed

- `MCP_SERVICE_TOKEN` and `EDA_OAUTH_ENABLED` are mutually exclusive — setting both raises at import time.
- In OAuth mode, outbound EasyDeploy API calls use only the per-request bearer; `EDA_API_KEY` is not used as a fallback.
- `EDA_API_BASE` is optional; the client defaults to the production EasyDeploy API. Set it only for custom or staging endpoints.

### Security

- `MCP_SERVICE_TOKEN` comparison uses `hmac.compare_digest` (constant-time).
- Explicit `verify=True` on all outbound `httpx` clients.
- HTTPS enforced on `authorization_endpoint` and `token_endpoint` URLs from OIDC discovery before use.
- `EDA_MCP_OAUTH_ISSUER` override validated as HTTPS.
- `assert` guards replaced with proper runtime checks (asserts are disabled under `python -O`).

## [0.1.0] - 2026-04-07

### Added

- Initial public release of the **EasyDeploy AI** MCP server as installable package `easydeploy-ai-mcp`.
- **stdio** entrypoint (`easydeploy-ai-mcp-stdio`, `python -m easydeploy_ai_mcp`) and **HTTP** entrypoint (`easydeploy-ai-mcp-http`, ASGI `easydeploy_ai_mcp.http_main:app`) with Streamable MCP on `/mcp` and `GET /healthz`.
- 24 tools covering EasyDeploy public REST operations: projects, datasets, uploads, models, training, predictions, account.
- Optional `MCP_SERVICE_TOKEN` for gating the HTTP MCP surface; HTTPS-only calls to the EasyDeploy API.
- `Dockerfile` for self-hosted deployments.

[0.1.0]: https://github.com/easydeploy-ai/easydeploy-ai-mcp/releases/tag/v0.1.0
