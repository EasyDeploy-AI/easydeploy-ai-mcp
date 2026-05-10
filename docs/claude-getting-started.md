# Using EasyDeploy AI with Claude (MCP)

This guide is for **EasyDeploy AI customers** who want to use **Claude** with the EasyDeploy MCP integration. You need an EasyDeploy account and the connection details your team or EasyDeploy provides.

**Hosting the MCP server on AWS or Docker** (for a shared remote URL) is covered in the [README](../README.md), not here.

---

## Before you start

- **EasyDeploy API key:** From the dashboard (**Account → API Keys**). You need this for **local MCP** on your computer (`claude_desktop_config.json`). The client talks to the **production EasyDeploy API** by default. You do **not** set an API base URL for normal use.
- **Remote MCP URL:** For the **hosted connector**, use the HTTPS MCP endpoint EasyDeploy provides (for most customers: `https://mcp.easydeploy.ai/mcp`). If you self-host the MCP server, your administrator will give you a different URL.

Claude’s remote MCP requirements (HTTPS, reachability from Anthropic) are described in [Anthropic: custom connectors](https://support.claude.com/en/articles/11503834-building-custom-connectors-via-remote-mcp-servers).

Step-by-step screenshots for the hosted flow are on the EasyDeploy site: [easydeploy.ai/claude-mcp](https://www.easydeploy.ai/claude-mcp) (or `/claude-mcp` on your deployment).

---

## Hosted connector (Claude Connectors)

We host the MCP endpoint. You add it once inside Claude; after you connect and sign in, new chats can use EasyDeploy like any other enabled connector.

1. Open Claude in the browser ([claude.ai](https://claude.ai)) or open **Claude Desktop**.
2. Open **Settings**, then **Connectors**, and choose **Add custom connector**.
3. Enter exactly:
   - **Name:** `EasyDeploy AI`
   - **Remote MCP server URL:** `https://mcp.easydeploy.ai/mcp` (or the URL your administrator gave you)
4. Save the connector so EasyDeploy appears in your list of connectors.
5. Open the EasyDeploy connector entry and choose **Connect**, then finish sign-in in your browser. That step authorizes Claude to use your EasyDeploy account.

### Connect and sign in (inside Claude)

After the custom connector is set up, Claude shows an EasyDeploy card with the MCP URL and a **Connect** button. Use that flow to sign in. You do not paste an API key into Claude. Access stays tied to the EasyDeploy profile you authenticate in the browser.

### Advanced settings (self-hosted or custom Cognito client)

If you are **not** using the default hosted URL above, expand **Advanced settings** in the Add custom connector flow as your administrator directs:

- **OAuth Client ID (optional)** — Leave empty for the hosted connector (`https://mcp.easydeploy.ai/mcp`). Your administrator provides this if you connect to a self-hosted MCP instance with a custom Cognito app client.
- **OAuth Client Secret (optional)** — Leave empty; the EasyDeploy MCP OAuth client is public (PKCE) and has no secret.

If your setup uses a **bearer token** for the MCP server, your administrator will tell you where to enter it in Claude (wording in the app can change between versions).

---

## Claude Desktop: file uploads and network egress

File uploads and some tool calls reach EasyDeploy over the network. On **Claude Desktop**, outbound traffic is blocked unless you allow the domains Claude should call.

1. Open **Settings → Capabilities**.
2. Turn on **Allow network egress**.
3. Under the domain allowlist, add this entry exactly (including the leading `*.`):

   `*.execute-api.us-east-1.amazonaws.com`

The domain allowlist UI is available on **paid** Claude plans.

---

## Local MCP (`claude_desktop_config.json`)

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

**Note:** Remote MCP URLs belong in **Connectors** (hosted connector above), not in this file.

---

## Claude on the web (claude.ai)

If your plan includes **custom connectors**, use **Hosted connector** above: add a connector and paste the **Remote MCP server URL** you were given. Menu labels may differ slightly from Claude Desktop.

---

## More help

- **Connection errors or security questions:** [claude.md](claude.md) (transports, tokens, health checks).
- **Install or run the HTTP server / Docker / AWS:** [README.md](../README.md).
