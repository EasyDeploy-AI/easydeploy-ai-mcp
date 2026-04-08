# Security

## Supported versions

Security fixes are applied to the **latest minor release** on the default branch (currently **0.1.x**). Older versions may not receive backports unless explicitly documented in a security advisory. Use the newest published release when deploying.

## Reporting vulnerabilities

Email **security@easydeploy.ai** with a clear description, reproduction steps, and impact. **Do not** open public GitHub issues for undisclosed security bugs or CVEs.

We aim to acknowledge receipt within **five business days** and will work with you on severity, fix timeline, and coordinated disclosure where appropriate.

## Remote deployment

- Terminate TLS at your load balancer (e.g. AWS ALB + ACM). Do not expose plain HTTP to the public internet.
- Store `EDA_API_KEY` and any `MCP_SERVICE_TOKEN` in a secrets manager (e.g. AWS Secrets Manager), not in images or source control.
- This server forwards tool calls to the **EasyDeploy public API** over HTTPS only (`api_client` rejects non-HTTPS base URLs).

### Auth modes for the HTTP transport

Pick exactly one. Setting both `EDA_OAUTH_ENABLED` and `MCP_SERVICE_TOKEN` raises at import time.

1. **OAuth 2.0 resource server (recommended for multi-tenant deployments).**
   Set `EDA_OAUTH_ENABLED=1`, `EDA_COGNITO_USER_POOL_ID`, `EDA_COGNITO_CLIENT_ID`,
   and optionally `EDA_COGNITO_REGION` (default `us-east-1`). Install the
   optional extra: `pip install easydeploy-ai-mcp[oauth]`.

   - Cognito **access** JWTs are validated locally against the Cognito JWKS:
     issuer, RS256 signature, `exp`, `token_use == 'access'`, and
     `client_id == EDA_COGNITO_CLIENT_ID`. Cognito access tokens carry
     `client_id`, **not** `aud` — do not configure an `audience` here.
   - EasyDeploy API keys (prefix `eda_live_`) are accepted in the same
     `Authorization: Bearer` header and forwarded verbatim. They are NOT
     validated locally; the EasyDeploy API is the source of truth for
     revocation and expiry.
   - The verified token is forwarded to the downstream API as
     `Authorization: Bearer <token>`. The API independently re-verifies.
   - `/.well-known/oauth-protected-resource` (RFC 9728) is published so
     MCP clients can discover the authorization server from a 401.

2. **Shared-secret gate (legacy single-tenant).** Set `MCP_SERVICE_TOKEN`.
   All MCP requests must present `Authorization: Bearer <MCP_SERVICE_TOKEN>`.
   Outbound API calls use the static `EDA_API_KEY` env var.

3. **No auth (development only).** Do not deploy publicly.

## API keys

Create and rotate keys from the EasyDeploy product: **Account → API Keys**. See official product and API documentation for details.

## Data handling

The MCP tools may pass project metadata, dataset/model identifiers, and prediction inputs to the EasyDeploy API. Treat logs and traces as sensitive; avoid logging full request bodies or API keys.
