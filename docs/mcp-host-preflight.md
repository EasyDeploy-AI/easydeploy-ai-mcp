# MCP host preflight (sandbox stack vs `.env.example`)

Run this **before** deploying the HTTP MCP to AWS or connecting Claude, so the **same** Cognito pool, MCP app client, and API base you used for local Docker smoke still match the deployed Amplify stack.

## 1. Compare CloudFormation / Amplify outputs to [`.env.example`](../.env.example)

| Item | `.env.example` (sandbox reference) | Where to verify |
|------|-----------------------------------|-----------------|
| `EDA_API_BASE` | `https://YOUR_API_ID.execute-api.us-east-1.amazonaws.com/prod` | API Gateway stage URL for your sandbox; must not 404 on `/v1/account` (unauthenticated → 401). |
| `EDA_COGNITO_USER_POOL_ID` | `us-east-1_XXXXXXXXX` | CloudFormation / Cognito console. |
| `EDA_COGNITO_CLIENT_ID` | `your-cognito-client-id` | Output **`McpClaudeOauthUserPoolClientId`** — app client **`easydeploy-mcp-claude-oauth`**, **not** the web SPA client. |
| `EDA_COGNITO_REGION` | `us-east-1` | Pool region. |

If the sandbox was **redeployed**, ids or the execute-api URL may have changed — update ECS task env (or CDK context) to match.

## 2. API authorizer accepts MCP client JWTs

The public API **Lambda authorizer** and `/api-keys` routes must accept access tokens issued to **`USER_POOL_MCP_CLIENT_ID`** as well as the web client. That wiring lives in your Amplify backend (e.g. `amplify/backend.ts`). If MCP tokens get **403** on `/v1/*` while `/mcp` works, redeploy backend with the MCP client id in the verifier allowlist.

## 3. Automated curl checks

From repo root (optional defaults match `.env.example`):

```bash
./scripts/verify_mcp_host_preflight.sh
```

Override:

```bash
export EDA_API_BASE='https://your-execute-api.../prod'
export EXPECTED_ISSUER='https://cognito-idp.us-east-1.amazonaws.com/us-east-1_XXXXXXXXX'
./scripts/verify_mcp_host_preflight.sh
```

## 4. Hosted UI domain

For PKCE token acquisition and Claude OAuth, note the Cognito **Hosted UI** domain (check your Cognito console under **App integration > Domain**).
