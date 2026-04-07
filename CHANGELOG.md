# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **EDA_API_BASE** is optional: the client defaults to the production EasyDeploy API (`https://api.easydeploy.ai`). Set it only for internal or staging endpoints. User-facing docs now emphasize **`EDA_API_KEY`** only.

## [0.1.0] - 2026-04-07

### Added

- Initial public release of the **EasyDeploy AI** MCP server as installable package **`easydeploy-ai-mcp`**.
- **stdio** entrypoint (`easydeploy-ai-mcp-stdio`, `python -m easydeploy_ai_mcp`) and **HTTP** entrypoint (`easydeploy-ai-mcp-http`, ASGI `easydeploy_ai_mcp.http_main:app`) with Streamable MCP on `/mcp` and `GET /healthz`.
- Tools covering EasyDeploy public REST operations (projects, datasets, uploads, models, training, predictions, account).
- Optional **`MCP_SERVICE_TOKEN`** for gating the HTTP MCP surface; HTTPS-only calls to the EasyDeploy API.
- Documentation: README, AWS deployment notes (`docs/aws-p0.md`), Claude / connectors guide (`docs/claude.md`), Docker image.

[0.1.0]: https://github.com/easydeploy-ai/easydeploy-ai-mcp/releases/tag/v0.1.0
