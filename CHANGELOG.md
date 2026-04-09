# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **MCP OAuth discovery (Claude broker):** RFC 9728 resource metadata lists the **MCP host** in `authorization_servers`, and **`GET /.well-known/oauth-authorization-server`** returns RFC 8414-style JSON whose **`authorization_endpoint` / `token_endpoint`** are **`{mcp_issuer}/authorize`** and **`{mcp_issuer}/token`** (not Cognito URLs), so every client hits the HTTP proxy. Cognito’s pool issuer does not implement `oauth-authorization-server` (HTTP 400). The proxy forwards to Cognito using URLs from **`openid-configuration`**. **`resource`** (RFC 8707) is stripped on **`/authorize`** and **`/token`** before forwarding — Cognito often rejects broker-sent values. Access tokens remain Cognito JWTs (`iss` unchanged). Optional **`EDA_MCP_OAUTH_ISSUER`** when the proxy does not set `Host` / `X-Forwarded-Proto` correctly. Module **`oauth_as_metadata.py`**.
- **Static DCR (RFC 7591):** Proxy AS metadata includes **`registration_endpoint`** (`{mcp_issuer}/oauth/register`). **`POST /oauth/register`** and **`POST /register`** return **201** with **`client_id`** = **`EDA_COGNITO_CLIENT_ID`** (public client, **`token_endpoint_auth_method`: `none`**). Cognito has no native DCR; **`/register`** matches brokers that POST issuer-relative registration.
- **Claude.ai token/authorize proxy:** **`GET /authorize`** redirects to Cognito's **`authorization_endpoint`** with the query string preserved; **`POST /token`** and **`GET /token`** forward to Cognito's **`token_endpoint`** (GET converts query string to a form POST body — some Claude.ai builds use **GET /token** per `anthropics/claude-ai-mcp#82`). Works around brokers that call **`{mcp_origin}/token`** instead of metadata (e.g. **`mcp_token_exchange_failed`**). Endpoints are read from Cognito **`openid-configuration`** (not hardcoded hosts).

### Added

- **AWS hosting:** CDK stack **`EasyDeployMcpHost`** moved to internal **`accessible-ai-cdk`** (VPC + Fargate + ALB, **ECR** image, health **`/healthz`**, sandbox-tested task env; `-c certificateArn=…` for HTTPS). Build/push the **Dockerfile** from this repo to ECR, then deploy that stack (see **accessible-ai-cdk** **DEVELOPMENT.md**). This repo: **`docs/mcp-host-preflight.md`**, **`docs/claude-remote-connector.md`**, **`scripts/verify_mcp_host_preflight.sh`**, **`scripts/push_mcp_image_ecr.sh`**, **`scripts/smoke_mcp_remote.sh`**, **`.dockerignore`**. Removed **`infra/cdk`** from the OSS package.
- **HTTP:** set **`EDA_TRUST_FORWARDED_HEADERS=1`** so uvicorn trusts **`X-Forwarded-Proto`** behind ALB (correct RFC 9728 **`resource`** URL). Documented in **`.env.example`**.

- **Server:** skip **`load_dotenv()`** while **`pytest`** is imported so a repo-root `.env` does not flip OAuth on during unit tests; set **`EDA_FORCE_DOTENV=1`** to opt in.

- **Server:** validate **`EDA_API_BASE`** after normalization (HTTPS + hostname) at import to fail fast on malformed env.

- **`scripts/smoke-mcp-train-predict.mjs`** + **`scripts/mcp-streamable-client.mjs`** — Node **18+** end-to-end smoke: project, `start_upload` + gateway PUT, `complete_upload`, model version, `submit_training_job`, poll `get_model_version`, `run_prediction`, `list_predictions` (aligned with accessible-ai `smoke-public-api.mjs`).

- **`scripts/smoke-mcp-http.mjs`** — Node **18+** smoke test (same style as accessible-ai `smoke-public-api.mjs`): healthz, OAuth metadata, optional direct `GET …/v1/account`, MCP `initialize` + `tools/list` + **`tools/call`** (default `get_account_status`). Env matches `validate_mcp_sandbox.sh`.

- **E2E validation plan** [docs/e2e-mcp-pre-claude-validation-plan.md](docs/e2e-mcp-pre-claude-validation-plan.md): phased checklist (pytest, REST API, OAuth MCP, Docker, tool→API, gates before Claude).
- **Optional integration test** `tests/test_cognito_jwt_integration.py`: with `EDA_INTEGRATION_COGNITO_ACCESS_TOKEN` and `EDA_COGNITO_*` set, runs `initialize` + `tools/list` on the HTTP app using **real** Cognito JWKS verification (skipped in default CI).
- **Sandbox validation**: `docs/sandbox-mcp-validation.md` and `scripts/validate_mcp_sandbox.sh` for smoke-checking `EDA_API_BASE`, `/healthz`, RFC 9728 metadata, direct `GET …/v1/account`, and OAuth `/mcp` (initialize + `tools/list`) against the sandbox API Gateway host.
- **Docker image** now installs `easydeploy-ai-mcp[oauth]` so JWT verification works without a separate pip extra in the container.
- **OAuth 2.0 resource-server mode** for the HTTP transport. Set `EDA_OAUTH_ENABLED=1` plus `EDA_COGNITO_USER_POOL_ID` / `EDA_COGNITO_CLIENT_ID` (and optional `EDA_COGNITO_REGION`) to validate incoming `Authorization: Bearer <jwt>` headers locally against the Cognito JWKS and forward the user's token to the EasyDeploy REST API. API keys (`eda_live_*`) are accepted in the same header and forwarded verbatim — the API is the source of truth for revocation. Optional install: `pip install easydeploy-ai-mcp[oauth]`.
- **`/.well-known/oauth-protected-resource`** (RFC 9728) endpoint published in OAuth mode so MCP clients can discover the Cognito authorization server from a 401 response. Unauthorized responses now include a `WWW-Authenticate: Bearer …` header.
- **`auth.py`** and **`credentials.py`** modules. Tools resolve their bearer token via a single helper that prefers a per-request token (HTTP/OAuth) and falls back to `EDA_API_KEY` (stdio).
- **`scripts/cognito_mcp_get_access_token.py`** — PKCE helper: uses **certifi** when installed (now in `.[dev]`), **`--insecure-ssl`** for dev token POST only, **`--authorize-path`** for unusual custom domains; clearer SSL error hints.
- **`scripts/run_mcp_docker_local.sh`** — build [`Dockerfile`](Dockerfile) and run MCP HTTP locally with optional root **`.env`** or extra `docker run` args.
- **`scripts/validate_sandbox_api_key.sh`** — two-step `GET …/v1/account` check using **`EDA_API_KEY`** only (no MCP).

### Fixed

- **`api_client`:** every REST helper used via `server._kw()` now accepts **`caller_channel`** and forwards it to **`X-Caller-Channel`** (was missing on most endpoints, causing `TypeError: … unexpected keyword argument 'caller_channel'` when calling tools such as **`list_projects`**).

### Changed

- **`.env.example`:** default **`EDA_API_BASE`** to the documented sandbox execute-api URL so Docker OAuth matches `validate_mcp_sandbox.sh` / `smoke-mcp-http.mjs` (avoids tools calling production while smoke uses sandbox).

- **Docs:** Sandbox reference (pool id, client id, issuer, API base) and concrete Phase 1–3 commands in [e2e-mcp-pre-claude-validation-plan.md](docs/e2e-mcp-pre-claude-validation-plan.md); matching Docker/OAuth examples in [sandbox-mcp-validation.md](docs/sandbox-mcp-validation.md); CI note on non-secret identifiers; PKCE troubleshooting in sandbox doc.

- **OAuth HTTP:** outbound EasyDeploy API calls use **only** the per-request `Authorization` bearer — **`EDA_API_KEY` is not used as a fallback** when `EDA_OAUTH_ENABLED=1` (stdio and non-OAuth HTTP unchanged).

- **`validate_mcp_sandbox.sh`** accepts **`EDA_API_KEY`** when `EDA_SMOKE_API_KEY` is unset, and **`EDA_INTEGRATION_COGNITO_ACCESS_TOKEN`** when `EDA_SMOKE_ACCESS_TOKEN` is unset.

- Documented **`EDA_COGNITO_CLIENT_ID`** as the Amplify output **`McpClaudeOauthUserPoolClientId`** (MCP OAuth client, not the web SPA client). Updated **`.env.example`**, **README**, **docs/aws-p0.md**, and **docs/claude-getting-started.md** accordingly.
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
