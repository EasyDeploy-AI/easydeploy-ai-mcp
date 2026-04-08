# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **OAuth 2.0 resource-server mode** for the HTTP transport. Set `EDA_OAUTH_ENABLED=1` plus `EDA_COGNITO_USER_POOL_ID` / `EDA_COGNITO_CLIENT_ID` (and optional `EDA_COGNITO_REGION`) to validate incoming `Authorization: Bearer <jwt>` headers locally against the Cognito JWKS and forward the user's token to the EasyDeploy REST API. API keys (`eda_live_*`) are accepted in the same header and forwarded verbatim — the API is the source of truth for revocation. Optional install: `pip install easydeploy-ai-mcp[oauth]`.
- **`/.well-known/oauth-protected-resource`** (RFC 9728) endpoint published in OAuth mode so MCP clients can discover the Cognito authorization server from a 401 response. Unauthorized responses now include a `WWW-Authenticate: Bearer …` header.
- **`auth.py`** and **`credentials.py`** modules. Tools resolve their bearer token via a single helper that prefers a per-request token (HTTP/OAuth) and falls back to `EDA_API_KEY` (stdio).

### Changed

- **EDA_API_BASE** is optional: the client defaults to the production EasyDeploy API (`https://api.easydeploy.ai`). Set it only for internal or staging endpoints. User-facing docs now emphasize **`EDA_API_KEY`** only.
- `MCP_SERVICE_TOKEN` and `EDA_OAUTH_ENABLED` are now mutually exclusive. Setting both fails fast at import time — the legacy shared-secret gate would otherwise consume the `Authorization` header before OAuth validation.

## [0.1.0] - 2026-04-07

### Added

- Initial public release of the **EasyDeploy AI** MCP server as installable package **`easydeploy-ai-mcp`**.
- **stdio** entrypoint (`easydeploy-ai-mcp-stdio`, `python -m easydeploy_ai_mcp`) and **HTTP** entrypoint (`easydeploy-ai-mcp-http`, ASGI `easydeploy_ai_mcp.http_main:app`) with Streamable MCP on `/mcp` and `GET /healthz`.
- Tools covering EasyDeploy public REST operations (projects, datasets, uploads, models, training, predictions, account).
- Optional **`MCP_SERVICE_TOKEN`** for gating the HTTP MCP surface; HTTPS-only calls to the EasyDeploy API.
- Documentation: README, AWS deployment notes (`docs/aws-p0.md`), Claude / connectors guide (`docs/claude.md`), Docker image.

[0.1.0]: https://github.com/easydeploy-ai/easydeploy-ai-mcp/releases/tag/v0.1.0
