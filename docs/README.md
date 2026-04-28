# Documentation index

Extra guides for **easydeploy-ai-mcp** beyond the [project README](../README.md).

| Document | Description |
|----------|-------------|
| [claude-getting-started.md](claude-getting-started.md) | EasyDeploy + Claude: remote Connectors or local `claude_desktop_config.json`. |
| [claude.md](claude.md) | Claude Connectors vs Claude Code, transports, headers, and health checks. |
| [aws-p0.md](aws-p0.md) | Lean AWS deployment (ALB, Fargate, secrets, WAF) and compliance notes for self-hosted HTTP MCP. |
| [mcp-host-preflight.md](mcp-host-preflight.md) | Before AWS deploy: compare Amplify/Cognito/API outputs to `.env.example`; `verify_mcp_host_preflight.sh`. |
| [claude-remote-connector.md](claude-remote-connector.md) | After deploy: DNS, RFC 9728 check, Claude custom connector URL, remote smoke. |
| [sandbox-mcp-validation.md](sandbox-mcp-validation.md) | Sandbox checklist, `validate_mcp_sandbox.sh`, Node `smoke-mcp-http.mjs`, and `smoke-mcp-train-predict.mjs` (train + predict via MCP). |
| [e2e-mcp-pre-claude-validation-plan.md](e2e-mcp-pre-claude-validation-plan.md) | Full E2E + integration order: pytest, REST API, OAuth MCP local/Docker, gates before Claude. |
| [aws-p0.md](aws-p0.md) (CDK hosting) | Stack **EasyDeployMcpHost**: VPC + Fargate + ALB, ECR image; see your CDK repo’s **DEVELOPMENT.md**. |
