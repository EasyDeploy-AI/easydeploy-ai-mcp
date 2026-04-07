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

## API keys

Create and rotate keys from the EasyDeploy product: **Account → API Keys**. See official product and API documentation for details.

## Data handling

The MCP tools may pass project metadata, dataset/model identifiers, and prediction inputs to the EasyDeploy API. Treat logs and traces as sensitive; avoid logging full request bodies or API keys.
