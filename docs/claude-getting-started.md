# Using EasyDeploy AI with Claude (MCP)

This guide is for **EasyDeploy AI customers** who want to use **Claude** with the EasyDeploy MCP integration. You need an EasyDeploy account and the connection details your team or EasyDeploy provides.

**Hosting the MCP server on AWS or Docker** (for a shared remote URL) is covered in the [README](../README.md), not here.

---

## Before you start

- **EasyDeploy API key:** From the dashboard (**Account → API Keys**). You need this for **Option B** (local MCP on your computer). The MCP client talks to the **production EasyDeploy API** by default—you do **not** set an API base URL for normal use. For **Option A** (remote connector), you usually only use the **Remote MCP URL** your admin gives you.
- **Remote MCP URL:** For Option A, the HTTPS address for the MCP endpoint (typically ending in `/mcp`). Use the value EasyDeploy or your administrator gives you.

Claude’s remote MCP requirements (HTTPS, reachability from Anthropic) are described in [Anthropic: custom connectors](https://support.claude.com/en/articles/11503834-building-custom-connectors-via-remote-mcp-servers).

Screenshots for Option A will be added here later.

---

## Option A — Remote MCP (Claude Desktop Connectors)

1. Open **Settings** → **Connectors** → **Add custom connector**.
2. Fill in the form:
  - **Name:** Any label you like (e.g. `EasyDeploy`).
  - **Remote MCP Server URL:** Paste the MCP URL you were given (must be `https://…` and include the `/mcp` path if that is how it was provided).
3. Expand **^ Advanced Settings**:
  - **OAuth Client ID (optional)** — If your team uses **Cognito OAuth** on the MCP server, your administrator may give you the **MCP app client id** (EasyDeploy backend CloudFormation output `McpClaudeOauthUserPoolClientId`). Paste it here when instructed; otherwise leave empty.
  - **OAuth Client Secret (optional)** — Only if your administrator issued a **confidential** client; the standard EasyDeploy MCP Cognito client is **public** (PKCE) and has no secret.
4. Save the connector, then use Claude as usual. You should see EasyDeploy-related tools when the connection succeeds.

If your setup uses a **bearer token** for the MCP server, your administrator will tell you where to enter it in Claude (wording in the app can change between versions).

---

## Option B — Local MCP (`claude_desktop_config.json`)

Use this when **Claude Desktop** runs the MCP server **on your computer** (stdio). Nothing is exposed to the internet; you only need your **API key** in the config (production API host is built in).

### 1. Install the MCP package

```bash
pip install easydeploy-ai-mcp
```

Confirm the launcher is available (or use the `python3` variant in the JSON below):

```bash
which easydeploy-ai-mcp-stdio
```

### 2. Edit Claude Desktop’s config

Create or edit this file (paths are typical; adjust if your install uses a different location):


| OS      | File                                                              |
| ------- | ----------------------------------------------------------------- |
| macOS   | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json`                     |


Merge the following into the **root** of the JSON object. If the file already has other keys (for example `globalShortcut`), keep them and only add or replace `mcpServers`.

Replace `eda_live_YOUR_KEY` with your key from **Account → API Keys**. Use the **full path** to `easydeploy-ai-mcp-stdio` for `command` if Claude does not find it on your `PATH` (run `which easydeploy-ai-mcp-stdio` in a terminal to copy the path). Set **`EDA_API_BASE`** in `env` only if EasyDeploy support asked you to use a non-production endpoint (internal testing).

```json
{
  "mcpServers": {
    "EasyDeploy AI": {
      "command": "easydeploy-ai-mcp-stdio",
      "args": [],
      "env": {
        "EDA_API_KEY": "eda_live_YOUR_KEY"
      }
    }
  }
}
```

**Alternative** if you prefer not to rely on the `stdio` script on `PATH`:

```json
{
  "mcpServers": {
    "EasyDeploy AI": {
      "command": "python3",
      "args": ["-m", "easydeploy_ai_mcp"],
      "env": {
        "EDA_API_KEY": "eda_live_YOUR_KEY"
      }
    }
  }
}
```

Use the same `python3` (or `python`) that has `easydeploy-ai-mcp` installed, with a **full path** to that interpreter if needed.

### 3. Restart Claude Desktop

Fully quit and reopen the app so it reloads the config. You should see **EasyDeploy AI** in your MCP servers and the EasyDeploy tools.

**Note:** Remote MCP URLs belong in **Connectors** (Option A), not in this file.

---

## Claude on the web (claude.ai)

If your plan includes **custom connectors**, use Option A: add a connector and paste the **Remote MCP Server URL** you were given. Menu labels may differ slightly from Claude Desktop.

---

## More help

- **Connection errors or security questions:** [claude.md](claude.md) (transports, tokens, health checks).
- **Install or run the HTTP server / Docker / AWS:** [README.md](../README.md).

