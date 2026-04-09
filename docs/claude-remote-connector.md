# Connect Claude to the hosted MCP (OAuth)

After the load balancer serves **`https://<your-domain>/mcp`** with a valid ACM certificate and the ECS task runs with the **tested sandbox env** (see [`.env.example`](../.env.example); deploy via **accessible-ai-cdk** **`EasyDeployMcpHost`** — **DEVELOPMENT.md** in that repo):

## 1. DNS

Create a **CNAME** (or Route53 alias) from your hostname (e.g. `mcp.sandbox.example.com`) to the **ALB DNS name** output by the stack (`LoadBalancerDns`).

The ACM certificate **must** cover this hostname (same region as the ALB, usually `us-east-1`).

## 2. Verify metadata (RFC 9728)

```bash
curl -sS "https://<your-domain>/.well-known/oauth-protected-resource" | jq .
```

- `authorization_servers[0]` should be **`https://<your-domain>`** (the MCP origin, not Cognito). Brokers load **`GET https://<your-domain>/.well-known/oauth-authorization-server`**, which this server implements by proxying **`authorization_endpoint` / `token_endpoint`** from Cognito’s **`openid-configuration`**. (Cognito’s pool issuer URL does not serve `oauth-authorization-server` and returns **400** if used as the AS identifier.)
- `resource` must be exactly `https://<your-domain>/mcp`. If it shows `http://` or the wrong host, ensure the task sets **`EDA_TRUST_FORWARDED_HEADERS=1`** (set on **`EasyDeployMcpHost`** tasks) and the ALB forwards `X-Forwarded-Proto`. If `authorization_servers` still shows the wrong host, set **`EDA_MCP_OAUTH_ISSUER=https://<your-domain>`** on the task.

## 3. Claude custom connector

1. In Claude (web or app), add a **custom connector** / **remote MCP** using URL **`https://<your-domain>/mcp`**.
2. Complete **browser OAuth**. You should hit **Cognito Hosted UI** and sign in with a user allowed to use the sandbox API.
3. Confirm the **access** token (not ID token): `token_use` = `access`, `client_id` = your **`McpClaudeOauthUserPoolClientId`**, `iss` = Cognito issuer.

If Cognito returns **`redirect_mismatch`**, the client’s `redirect_uri` is not allowlisted — add it in Amplify [`backend.ts`](https://github.com/easydeploy-ai/accessible-ai/blob/main/amplify/backend.ts) MCP app client `callbackUrls`, then redeploy. See [cognito-mcp-claude-oauth.md](https://github.com/easydeploy-ai/accessible-ai/blob/main/docs/operations/cognito-mcp-claude-oauth.md).

## 4. Smoke from your laptop

```bash
export MCP_SMOKE_BASE_URL='https://<your-domain>'
export EDA_SMOKE_ACCESS_TOKEN='...'   # from PKCE script
./scripts/smoke_mcp_remote.sh
```

Anthropic: [Building custom connectors via remote MCP servers](https://support.anthropic.com/en/articles/11503834-building-custom-connectors-via-remote-mcp-servers).
