# EasyDeploy AI MCP (`easydeploy-ai-mcp`)

A [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server that exposes the **[EasyDeploy](https://easydeploy.ai)** public REST API as tools for Claude, Cursor, Claude Code, and other MCP clients.

**PyPI package name:** `easydeploy-ai-mcp` · **Import package:** `easydeploy_ai_mcp`

## Contents

- [Connect with Claude](#connect-with-claude)
- [What you get](#what-you-get)
- [Requirements](#requirements)
- [Install](#install)
- [Environment variables](#environment-variables)
- [Local MCP (stdio)](#local-mcp-stdio)
- [Remote MCP (HTTP)](#remote-mcp-http)
- [Documentation](#documentation)
- [REST API reference](#rest-api-reference)
- [Releases and API compatibility](#releases-and-api-compatibility)
- [Development](#development)
- [Security](#security)
- [License](#license)

## Connect with Claude

### Option A — Hosted connector (zero setup)

The fastest way to use EasyDeploy AI tools in Claude. No installation required.

1. Open **Claude** (web at [claude.ai](https://claude.ai) or Claude Desktop).
2. Go to **Settings → Connectors → Add custom connector**.
3. Fill in the form:
   - **Name:** `EasyDeploy AI`
   - **URL:** `https://mcp.easydeploy.ai/mcp`
4. Complete the OAuth sign-in flow when prompted. Claude will redirect you to EasyDeploy to authenticate with your account.
5. Save the connector. EasyDeploy tools will appear in your Claude sessions.

> **Note:** The hosted connector uses OAuth — you sign in with your EasyDeploy account; no API key needs to be pasted into Claude.

---

### Option B — Self-hosted (local stdio)

Run the MCP server on your own machine using your EasyDeploy API key. Nothing is exposed to the internet.

**1. Install**

```bash
pip install easydeploy-ai-mcp
```

**2. Add to Claude Desktop config**

Edit (or create) the Claude Desktop config file:

| OS      | Path |
| ------- | ---- |
| macOS   | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

Merge the following into the root of that JSON (keep any existing keys):

```json
{
  "mcpServers": {
    "EasyDeploy AI": {
      "command": "easydeploy-ai-mcp-stdio",
      "env": {
        "EDA_API_KEY": "eda_live_YOUR_KEY"
      }
    }
  }
}
```

Replace `eda_live_YOUR_KEY` with your key from **Account → API Keys** in the EasyDeploy dashboard. Use the full path to `easydeploy-ai-mcp-stdio` (run `which easydeploy-ai-mcp-stdio` to find it) if Claude cannot locate it on your `PATH`.

**3. Restart Claude Desktop**

Fully quit and reopen the app. **EasyDeploy AI** will appear in your MCP servers.

---

For self-hosting on AWS (Fargate + ALB) or Docker, see [Remote MCP (HTTP)](#remote-mcp-http) and [docs/aws-p0.md](docs/aws-p0.md).

## What you get

- **24 tools** covering projects, datasets (including upload flow), model versions, training jobs, predictions, and account status.
- **stdio** transport for local clients, or **HTTP** with Streamable MCP on `/mcp` and **GET /healthz** for load balancers.
- **Hardening:** HTTPS-only calls to the EasyDeploy API; optional `MCP_SERVICE_TOKEN` for the HTTP MCP surface; response fields trimmed where appropriate for agents.

For **production** and **SOC 2–sensitive** setups, prefer **self-hosted AWS** (ECS Fargate, ALB, Secrets Manager, CloudWatch). See [docs/aws-p0.md](docs/aws-p0.md). Third-party MCP hosting is fine for experiments but adds another subprocessor for compliance.

## Requirements

- Python **3.10+**
- An EasyDeploy **API key** from the dashboard (**Account → API Keys**). The client uses the production EasyDeploy API host by default.

## Install

### From PyPI (after first release)

```bash
pip install easydeploy-ai-mcp
```

### From source

```bash
git clone https://github.com/easydeploy-ai/easydeploy-ai-mcp.git
cd easydeploy-ai-mcp
pip install -e ".[dev]"   # includes pytest
# or minimal runtime only:
pip install .
```

## Environment variables


| Variable                           | Required | Description                                                                                     |
| ---------------------------------- | -------- | ----------------------------------------------------------------------------------------------- |
| `EDA_API_KEY`                      | stdio / legacy HTTP | Required for **stdio** and **legacy** HTTP (no OAuth). **Not** used for outbound API calls when `EDA_OAUTH_ENABLED=1` — each MCP request must include `Authorization: Bearer <JWT or eda_live_…>`. |
| `EDA_API_BASE`                     | No       | Overrides the default production API (`https://api.easydeploy.ai`). **Required in practice** for sandbox execute-api or when the container cannot resolve the default host. Same normalization rules (optional `/v1`). |
| `EDA_UI_BASE_URL`                  | No       | Prefix for `ui_url` fields (default `https://easydeploy.ai`).                                   |
| `MCP_SERVICE_TOKEN`                | No       | Legacy single-tenant gate. If set, HTTP mode requires `Authorization: Bearer <token>` for `/mcp` (not for `GET /healthz`). Mutually exclusive with `EDA_OAUTH_ENABLED`. |
| `EDA_OAUTH_ENABLED`                | No       | Set to `1` to run the HTTP transport as an OAuth 2.0 resource server. Requires `EDA_COGNITO_USER_POOL_ID` and `EDA_COGNITO_CLIENT_ID`. See [Remote MCP (HTTP)](#remote-mcp-http). |
| `EDA_COGNITO_USER_POOL_ID`         | OAuth    | Cognito user pool that issues access tokens for the EasyDeploy API.                             |
| `EDA_COGNITO_CLIENT_ID`            | OAuth    | App client ID expected in the access token's `client_id` claim.                                 |
| `EDA_COGNITO_REGION`               | No       | AWS region for the user pool (default `us-east-1`).                                             |
| `EDA_REPORT_MAX_WAIT_SECONDS`      | No       | `get_model_report` poll budget (default `300`).                                                 |
| `EDA_REPORT_POLL_INTERVAL_SECONDS` | No       | Poll interval in seconds (default `10`).                                                        |
| `HOST` / `PORT`                    | No       | HTTP bind (defaults `0.0.0.0` / `8080`).                                                        |
| `EDA_TRUST_FORWARDED_HEADERS`      | No       | Set to `1` behind ALB/reverse proxy so RFC 9728 `resource` uses `https` (trusts `X-Forwarded-Proto`). |
| `EDA_MCP_OAUTH_ISSUER`             | No       | Public MCP base URL (no path) for `authorization_servers` and proxy `/.well-known/oauth-authorization-server` **`issuer`**. Default: request origin. Use if `Host` / `X-Forwarded-Proto` are wrong behind a proxy. |


Copy [.env.example](.env.example) as a template (example values only; never commit real keys).

## Local MCP (stdio)

Use when the client **starts** the server as a subprocess (Claude Desktop, Cursor, etc.).

```bash
export EDA_API_KEY="eda_live_..."
easydeploy-ai-mcp-stdio
```

Or: `python -m easydeploy_ai_mcp`

Example config snippet:

```json
{
  "mcpServers": {
    "easydeploy-ai": {
      "command": "easydeploy-ai-mcp-stdio",
      "env": {
        "EDA_API_KEY": "eda_live_..."
      }
    }
  }
}
```

## Remote MCP (HTTP)

Serves **Streamable HTTP** via FastMCP on **`/mcp`** (confirm with your pinned **FastMCP 3.x** version). Health checks: **GET /healthz**.

```bash
export EDA_API_KEY="eda_live_..."
easydeploy-ai-mcp-http
```

Or: `uvicorn easydeploy_ai_mcp.http_main:app --host 0.0.0.0 --port 8080`

If you embed `mcp.http_app()` in another ASGI app, pass through **`lifespan`** from the FastMCP HTTP app ([FastMCP ASGI](https://gofastmcp.com/deployment/asgi)); `easydeploy_ai_mcp.http_main` already does this for uvicorn.

### Auth modes

Pick exactly one (setting both `EDA_OAUTH_ENABLED` and `MCP_SERVICE_TOKEN` raises at import):

- **OAuth 2.0 resource server** (multi-tenant): set `EDA_OAUTH_ENABLED=1` plus
  `EDA_COGNITO_USER_POOL_ID` and `EDA_COGNITO_CLIENT_ID`. Set **`EDA_COGNITO_CLIENT_ID`**
  to the **MCP OAuth** Cognito app client id (EasyDeploy Amplify deploy emits the
  CloudFormation output **`McpClaudeOauthUserPoolClientId`**) — not the web SPA
  client id. Install the optional
  extra: `pip install easydeploy-ai-mcp[oauth]`. The server validates incoming
  Cognito **access** JWTs locally against the Cognito JWKS (issuer, signature,
  `exp`, `token_use=='access'`, `client_id`) and forwards the token to the
  EasyDeploy API. EasyDeploy API keys (prefix `eda_live_`) are accepted in the
  same `Authorization: Bearer` header and forwarded as-is — the API is the
  source of truth for revocation. RFC 9728 metadata is published at
  `/.well-known/oauth-protected-resource`; RFC 8414 proxy metadata includes
  **`registration_endpoint`**, and **`POST /oauth/register`** returns the static
  Cognito MCP **`EDA_COGNITO_CLIENT_ID`** (RFC 7591-style, public client). 401
  responses include `WWW-Authenticate: Bearer …` so MCP clients can discover the
  auth server.
  Note: Cognito access tokens carry `client_id`, **not** `aud`; do not configure
  an audience.
- **Shared-secret gate** (legacy single-tenant): set `MCP_SERVICE_TOKEN`. All
  outbound API calls use the static `EDA_API_KEY`.
- **No auth**: development only.

**Docker — run locally** (same image you deploy to ECS/Fargate; includes `easydeploy-ai-mcp[oauth]`):

```bash
# Convenience: build + run; uses .env if present, else pass -e flags
./scripts/run_mcp_docker_local.sh

# Example: legacy API-key mode with explicit env (no .env)
./scripts/run_mcp_docker_local.sh \
  -e EDA_API_KEY="eda_live_..." \
  -e EDA_API_BASE="https://YOUR_API_ID.execute-api.us-east-1.amazonaws.com/prod"

# OAuth mode: put EDA_OAUTH_ENABLED, EDA_COGNITO_*, EDA_API_BASE in .env (omit EDA_API_KEY), then:
./scripts/run_mcp_docker_local.sh

# Different host port
PORT=9000 ./scripts/run_mcp_docker_local.sh
```

**Host on AWS (Fargate + ALB):** build and push this repo’s **Dockerfile** to ECR, then deploy behind an ALB with an ACM certificate. Task env vars match **`.env.example`**.

Manual equivalent:

```bash
docker build -t easydeploy-ai-mcp .
docker run --rm -p 8080:8080 \
  -e EDA_API_KEY="eda_live_..." \
  easydeploy-ai-mcp
```

## Documentation

- **[docs/claude-getting-started.md](docs/claude-getting-started.md)** — EasyDeploy + Claude: Connectors or local Desktop config JSON
- **[docs/claude.md](docs/claude.md)** — Claude Connectors vs Claude Code, transports, headers

## REST API reference

This MCP server is a thin client over the **EasyDeploy public REST API**. Endpoint behavior, request bodies, and response shapes are defined by **EasyDeploy** (dashboard, product help, and official API materials at [easydeploy.ai](https://easydeploy.ai)). This repo does not duplicate the full OpenAPI spec; it maps those operations to MCP tools.

## Releases and API compatibility

**This repository** is the open-source home of the EasyDeploy MCP server. New releases track the **EasyDeploy public REST API** as documented for customers (dashboard and official API materials). If the API adds or changes endpoints, expect corresponding updates here. Contributors should follow [CONTRIBUTING.md](CONTRIBUTING.md) when changing tools or client behavior.

## Development

```bash
pip install -e ".[dev]"
pytest
```

**Optional — real Cognito JWT against the HTTP app** (live JWKS, no mocks): set `EDA_INTEGRATION_COGNITO_ACCESS_TOKEN` plus the same `EDA_COGNITO_*` vars you use for OAuth mode, then run `pytest tests/test_cognito_jwt_integration.py -v`. See the docstring in that file.

See [CONTRIBUTING.md](CONTRIBUTING.md) for pull requests and reporting issues.

## Security

See [SECURITY.md](SECURITY.md) for vulnerability reporting and deployment notes.

## License

MIT — see [LICENSE](LICENSE).