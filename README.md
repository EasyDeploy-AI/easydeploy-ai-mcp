# EasyDeploy AI MCP (`easydeploy-ai-mcp`)

A [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server that exposes the **[EasyDeploy](https://easydeploy.ai)** public REST API as tools for Claude, Cursor, Claude Code, and other MCP clients.

**PyPI package name:** `easydeploy-ai-mcp` · **Import package:** `easydeploy_ai_mcp`

## Contents

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
| `EDA_API_KEY`                      | Yes      | Bearer token from the EasyDeploy dashboard.                                                     |
| `EDA_API_BASE`                     | No       | **Internal/staging only.** Overrides the default production API (`https://api.easydeploy.ai`). Same normalization rules (optional `/v1`). |
| `EDA_UI_BASE_URL`                  | No       | Prefix for `ui_url` fields (default `https://easydeploy.ai`).                                   |
| `MCP_SERVICE_TOKEN`                | No       | If set, HTTP mode requires `Authorization: Bearer <token>` for `/mcp` (not for `GET /healthz`). |
| `EDA_REPORT_MAX_WAIT_SECONDS`      | No       | `get_model_report` poll budget (default `300`).                                                 |
| `EDA_REPORT_POLL_INTERVAL_SECONDS` | No       | Poll interval in seconds (default `10`).                                                        |
| `HOST` / `PORT`                    | No       | HTTP bind (defaults `0.0.0.0` / `8080`).                                                        |


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

**Docker** (from the root of this repository):

```bash
docker build -t easydeploy-ai-mcp .
docker run --rm -p 8080:8080 \
  -e EDA_API_KEY="eda_live_..." \
  easydeploy-ai-mcp
```

## Documentation

- **[docs/README.md](docs/README.md)** — index of extra guides
- **[docs/claude-getting-started.md](docs/claude-getting-started.md)** — EasyDeploy + Claude: Connectors or local Desktop config JSON
- **[docs/claude.md](docs/claude.md)** — Claude Connectors vs Claude Code, transports, headers
- **[docs/aws-p0.md](docs/aws-p0.md)** — lean AWS deployment and security checklist

## REST API reference

This MCP server is a thin client over the **EasyDeploy public REST API**. Endpoint behavior, request bodies, and response shapes are defined by **EasyDeploy** (dashboard, product help, and official API materials at [easydeploy.ai](https://easydeploy.ai)). This repo does not duplicate the full OpenAPI spec; it maps those operations to MCP tools.

## Releases and API compatibility

**This repository** is the open-source home of the EasyDeploy MCP server. New releases track the **EasyDeploy public REST API** as documented for customers (dashboard and official API materials). If the API adds or changes endpoints, expect corresponding updates here. Contributors should follow [CONTRIBUTING.md](CONTRIBUTING.md) when changing tools or client behavior.

## Development

```bash
pip install -e ".[dev]"
pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for pull requests and reporting issues.

## Security

See [SECURITY.md](SECURITY.md) for vulnerability reporting and deployment notes.

## License

MIT — see [LICENSE](LICENSE).