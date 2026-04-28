# Claude and remote EasyDeploy AI MCP

**End-user setup (Connectors or local Desktop JSON)?** See [claude-getting-started.md](claude-getting-started.md).

How to use this MCP server with **Anthropic** clients: local **stdio** vs remote **HTTP**, and where to configure each.

## Transports

- **Local:** **stdio** — the Claude or Cursor host runs `easydeploy-ai-mcp-stdio` as a subprocess (or `python -m easydeploy_ai_mcp`). Set **`EDA_API_KEY`** on that process. **`EDA_API_BASE`** is only for internal/staging overrides; customers use the default production API.
- **Remote:** **HTTP** — this package serves **Streamable HTTP** via FastMCP on **`/mcp`**. Pin **FastMCP** and confirm behavior with your client version; legacy **HTTP+SSE** is being phased out across the ecosystem.

## Claude Desktop / claude.ai (Connectors)

Remote MCP servers are added in the **Claude** web or desktop product (**Settings** or **Customize**, then **Connectors** — exact labels can change; use Anthropic’s current UI). Do **not** put a remote HTTPS URL in `claude_desktop_config.json`; that file is for **local** stdio command-based servers only.

Requirements (see [Anthropic: custom connectors](https://support.claude.com/en/articles/11503834-building-custom-connectors-via-remote-mcp-servers)):

- **HTTPS** endpoint reachable from **Anthropic’s infrastructure** (not only from the user’s laptop). Allow **[Anthropic published egress IP ranges](https://support.claude.com/en/articles/11503834-building-custom-connectors-via-remote-mcp-servers)** on your load balancer or WAF if you use IP filtering.
- Prefer **Streamable HTTP** over legacy SSE where supported.
- **OAuth** is the long-term model for multi-user identity; P0 single-tenant deployments often use one `EDA_API_KEY` in the container plus optional **`MCP_SERVICE_TOKEN`** for the MCP HTTP surface.

## Claude Code

Claude Code supports **HTTP** MCP servers, for example:

```bash
claude mcp add --transport http easydeploy-ai https://your-alb.example.com/mcp \
  --header "Authorization: Bearer YOUR_MCP_SERVICE_TOKEN"
```

If `MCP_SERVICE_TOKEN` is unset on the server, omit the header. Users still need a valid EasyDeploy **`EDA_API_KEY`** in the **server** environment for API calls (unless you implement per-request credential forwarding or OAuth later).

## Health checks

ALB and orchestrators should use **`GET /healthz`**, which does **not** require `MCP_SERVICE_TOKEN`.
