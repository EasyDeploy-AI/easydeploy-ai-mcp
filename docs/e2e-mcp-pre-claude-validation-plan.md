# End-to-end MCP validation and integration testing (pre–Claude / pre–deploy)

Run this sequence **before** you deploy the HTTP MCP server to a shared URL and connect **Claude** (remote connector). It separates **build quality**, **upstream API correctness**, **Cognito OAuth**, and **MCP Streamable HTTP** so failures are easy to localize.

**Related docs**

| Topic | Document |
|--------|-----------|
| Sandbox API base + smoke script env | [sandbox-mcp-validation.md](sandbox-mcp-validation.md) |
| Cognito MCP client, callbacks, where to find ids | Your Amplify backend's Cognito MCP OAuth docs |
| ALB / Fargate / secrets for MCP host | [aws-p0.md](aws-p0.md), your CDK `EasyDeployMcpHost` stack |
| Claude connector fields | [claude-getting-started.md](claude-getting-started.md) |

---

## Goals

1. **Package and image** install with OAuth/JWT support (`easydeploy-ai-mcp[oauth]`).
2. **Public REST API** for the target stage responds on the expected **HTTPS base + `/v1`** with a known-good **API key** and with a **Cognito access token** (same pool / MCP client as production MCP will use).
3. **MCP HTTP app** accepts the same bearer at `/mcp` (Streamable HTTP: `Accept`, session), serves `/healthz` and (in OAuth mode) RFC 9728 metadata.
4. **Optional:** one **tool** call reaches the same API base (proves credential forwarding).

Until phases **0–4** pass, debugging Claude adds variables (Anthropic redirect URIs, public HTTPS, DNS). Complete them first.

---

## Preconditions

- [ ] Target **Amplify / CDK** stack deployed for the stage you are validating (e.g. **sandbox**).
- [ ] **`McpClaudeOauthUserPoolClientId`**, **`UserPoolId`**, **Hosted UI domain**, and **public API base** (`EDA_API_BASE` or execute-api URL) recorded for that stage.
- [ ] A valid **`eda_live_…`** API key for an account on that environment.
- [ ] Local tools: **Python 3.10+**, **curl**, **Docker** (if you validate the image), **AWS CLI** (optional, for `describe-user-pool-client`).

### Sandbox reference (non-secret identifiers)

The table below lists **public** sandbox values useful for copy-paste validation. They are **not** secrets, but they **can change** when stacks are redeployed—always confirm against **CloudFormation / Amplify Outputs** for your stage.

| Item | Example (EasyDeploy sandbox) |
|------|------------------------------|
| Region | `us-east-1` |
| `EDA_API_BASE` | `https://YOUR_API_ID.execute-api.us-east-1.amazonaws.com/prod` |
| User pool id | `us-east-1_XXXXXXXXX` |
| MCP OAuth app client id (`McpClaudeOauthUserPoolClientId`) | `your-cognito-client-id` |
| Cognito issuer (JWKS; JWT **`iss`** claim) | `https://cognito-idp.us-east-1.amazonaws.com/us-east-1_XXXXXXXXX` |
| MCP OAuth **`authorization_servers[0]`** (discovery) | `https://<mcp-host>` (same origin as `resource` without `/mcp`) |
| Hosted UI domain | From **Cognito → App integration → Domain**: default is `<prefix>.auth.us-east-1.amazoncognito.com` (prefix is pool-specific). A **custom** domain (e.g. `auth.sandbox.easydeploy.ai`) only works after Hosted UI + domain are fully active—if you see **Login pages unavailable**, use the default `*.amazoncognito.com` host for PKCE until fixed. |

---

## Phase 0 — Repository checks (no secrets)

**Purpose:** Catch regressions before any cloud dependency.

| Step | Command / action | Pass criteria |
|------|------------------|---------------|
| 0.1 | From `easydeploy-ai-mcp` repo: `pip install -e ".[dev]"` | Install succeeds |
| 0.2 | `pytest` | All unit tests pass; integration test skipped unless env set (expected in CI) |
| 0.3 | Optional: `docker build -t eda-mcp-test .` | Image builds; Dockerfile uses `".[oauth]"` |

---

## Phase 1 — Upstream REST API (no MCP)

**Purpose:** Confirm **stage**, **path**, and **authorizer** for the EasyDeploy public API.

Set (sandbox example; replace with your stage URL if different):

```bash
export EDA_API_BASE="https://YOUR_API_ID.execute-api.us-east-1.amazonaws.com/prod"
# Effective REST prefix: ${EDA_API_BASE}/v1/... after client normalization
```

| Step | Command / action | Pass criteria |
|------|------------------|---------------|
| 1.1 | `curl -sS -o /dev/null -w "%{http_code}" "$EDA_API_BASE/v1/account"` **without** `Authorization` | **401** (or 403), not **404** — proves `/v1` + stage |
| 1.2 | `curl -sS -o /dev/null -w "%{http_code}" -H "Authorization: Bearer eda_live_…" "$EDA_API_BASE/v1/account"` | **200** |
| 1.3 | (OAuth path) Obtain a Cognito **access** JWT for the **MCP app client** (PKCE script below). Then: `curl -sS -o /dev/null -w "%{http_code}" -H "Authorization: Bearer <access_jwt>" "$EDA_API_BASE/v1/account"` | **200** if that user is allowed; otherwise document expected code and fix authorizer / client id list |

**Token for 1.3:** from **this** repo (PKCE; needs `pip install -e ".[dev]"` for **certifi** on macOS python.org builds):

```bash
# Use your pool’s domain from Cognito (default *.amazoncognito.com is most reliable for local PKCE).
python3 scripts/cognito_mcp_get_access_token.py \
  --cognito-host "<prefix>.auth.us-east-1.amazoncognito.com" \
  --client-id "your-cognito-client-id"
# Then: export the printed EDA_SMOKE_ACCESS_TOKEN and curl Phase 1.3 with that bearer.
```

If the token **POST** fails with **`CERTIFICATE_VERIFY_FAILED`**, run macOS **Install Certificates.command**, or add `--insecure-ssl` (dev only). See [sandbox-mcp-validation.md](sandbox-mcp-validation.md#pkce-script-troubleshooting).

See the PKCE troubleshooting section in [sandbox-mcp-validation.md](sandbox-mcp-validation.md#pkce-script-troubleshooting).

---

## Phase 2 — MCP HTTP locally (OAuth mode)

**Purpose:** Validate **FastMCP + middleware + JWKS** without ALB or Claude.

Set (sandbox example):

```bash
export EDA_OAUTH_ENABLED=1
export EDA_COGNITO_USER_POOL_ID="us-east-1_XXXXXXXXX"
export EDA_COGNITO_CLIENT_ID="your-cognito-client-id"
export EDA_COGNITO_REGION="us-east-1"
export EDA_API_BASE="https://YOUR_API_ID.execute-api.us-east-1.amazonaws.com/prod"
unset MCP_SERVICE_TOKEN
```

| Step | Command / action | Pass criteria |
|------|------------------|---------------|
| 2.1 | `easydeploy-ai-mcp-http` (or `uvicorn easydeploy_ai_mcp.http_main:app`) on `PORT=8080` | Process starts, no import error |
| 2.2 | `curl -sS http://127.0.0.1:8080/healthz` | `{"status":"ok"}` |
| 2.3 | `curl -sS http://127.0.0.1:8080/.well-known/oauth-protected-resource` | **200**, JSON with `authorization_servers` |
| 2.4 | Run **`./scripts/validate_mcp_sandbox.sh`** with `MCP_SMOKE_BASE_URL=http://127.0.0.1:8080`, `EDA_SMOKE_API_KEY`, `EDA_SMOKE_ACCESS_TOKEN` | Script ends **OK** (see [sandbox-mcp-validation.md](sandbox-mcp-validation.md)) |
| 2.5 | Optional automated test: reuse the same Cognito **access** JWT (`export EDA_INTEGRATION_COGNITO_ACCESS_TOKEN="$EDA_SMOKE_ACCESS_TOKEN"` after PKCE) plus **`EDA_COGNITO_USER_POOL_ID`**, **`EDA_COGNITO_CLIENT_ID`**, **`EDA_COGNITO_REGION`**; then `pytest tests/test_cognito_jwt_integration.py -v` | **1 passed** (real JWKS + `initialize` + `tools/list`) |

---

## Phase 3 — MCP in Docker (same as Phase 2)

**Purpose:** Match what you run in ECS / Fargate.

| Step | Command / action | Pass criteria |
|------|------------------|---------------|
| 3.1 | `docker run --rm -p 8080:8080 -e EDA_OAUTH_ENABLED=1 -e EDA_API_BASE="https://YOUR_API_ID.execute-api.us-east-1.amazonaws.com/prod" -e EDA_COGNITO_USER_POOL_ID="us-east-1_XXXXXXXXX" -e EDA_COGNITO_CLIENT_ID="your-cognito-client-id" -e EDA_COGNITO_REGION="us-east-1" eda-mcp-test` | Container healthy |
| 3.2 | Repeat Phase 2.2–2.4 against `http://127.0.0.1:8080` | Same pass criteria |

---

## Phase 4 — Tool → API (optional but recommended)

**Purpose:** Prove the **bearer** used at `/mcp` is forwarded to the REST API.

| Step | Command / action | Pass criteria |
|------|------------------|---------------|
| 4.1 | With MCP running (Phase 2 or 3), complete MCP `initialize` + session, then `tools/call` for **`get_account_status`** with `{}` (or minimal args) using the **same access JWT** in `Authorization` | **No** tool error from auth; response reflects API (or a clear API error that is not 401 from missing token). Automate with **`node scripts/smoke-mcp-http.mjs`** (same bearer env as `validate_mcp_sandbox.sh`). |
| 4.2 | Optional deeper check: **train + ad-hoc predict** via MCP only (`start_upload` → PUT → `complete_upload` → … → `run_prediction`) | **`node scripts/smoke-mcp-train-predict.mjs --file <csv>`** completes (budget **~5–30+ min** for training on sandbox). |
| 4.2 | Correlate **API Gateway / Lambda** access logs for `GET …/v1/account` (or equivalent) | Request seen with expected stage |

If 4.1 fails with upstream **401**, check `EDA_API_BASE`, key vs JWT path, and authorizer **`USER_POOL_MCP_CLIENT_ID`** on the backend.

---

## Phase 5 — Pre–Claude deployment gate

Complete **before** publishing the **remote MCP URL** to Claude.

| # | Gate |
|---|------|
| G1 | Phases **0–3** documented above **passed** for the target stage |
| G2 | **HTTPS** MCP URL terminates with a valid certificate (staging/Let’s Encrypt/ACM as designed) |
| G3 | `GET https://<mcp-host>/healthz` returns **200** from the internet (or from a host that mimics Anthropic egress, if you have a proxy allowlist) |
| G4 | `GET https://<mcp-host>/.well-known/oauth-protected-resource` returns **200**, `authorization_servers` is **`["https://<mcp-host>"]`** (or the value of **`EDA_MCP_OAUTH_ISSUER`**), and `resource` is **`https://<mcp-host>/mcp`**. **`GET https://<mcp-host>/.well-known/oauth-authorization-server`** returns **200** JSON with **`issuer`** = that same MCP URL, **`registration_endpoint`** = `https://<mcp-host>/oauth/register`, and **`authorization_endpoint` / `token_endpoint`** pointing at your Cognito Hosted UI domain (not **400** on `cognito-idp…/oauth-authorization-server`). **`POST https://<mcp-host>/oauth/register`** with JSON body → **201** and **`client_id`** = your **`McpClaudeOauthUserPoolClientId`**. |
| G5 | Cognito **callback URLs** include Anthropic MCP redirects (see runbook); any new Claude **redirect_uri** added to CDK and redeployed |
| G6 | Smoke: `validate_mcp_sandbox.sh` (or equivalent) against **`https://<mcp-host>`** with real **`EDA_SMOKE_ACCESS_TOKEN`** | **OK** |
| G7 | **Runbook** updated with stage-specific **non-secret** references (pool id output names, API base pattern); secrets only in Parameter Store / Secrets Manager |

---

## Legacy path (API key only, no OAuth)

Use when Cognito is not ready yet:

- Leave **`EDA_OAUTH_ENABLED`** unset.
- Set **`EDA_API_BASE`** + **`EDA_API_KEY`**.
- Optionally **`MCP_SERVICE_TOKEN`** to gate `/mcp`.

Pass Phases **0**, **1.1–1.2**, **2.1–2.2** (no 2.3 metadata), and **3** without OAuth expectations. **Do not** point Claude OAuth at this mode; use API-key or shared-secret flows only as documented for your org.

---

## CI recommendation

- **Always:** Phase **0** (`pytest` without integration env).
- **Nightly or pre-release:** Phases **1–2** against a **dedicated test stage** using secrets in CI (API key + short-lived token or machine user), or a manual “release checklist” runbook execution.
- **Never commit:** JWTs, API keys, or refresh tokens.
- **OK to document in runbooks** (non-secret): user pool id, MCP app **client id**, execute-api **base URL** pattern, Cognito **issuer** URL—the same class of identifiers as in **Sandbox reference** above.

---

## Quick failure map

| Symptom | Likely layer |
|---------|----------------|
| `/v1/account` **404** | Wrong `EDA_API_BASE` / stage / missing `/v1` |
| `/v1/account` **401** with API key | Key invalid for that stage |
| `/v1/account` **403** with JWT | Authorizer or `USER_POOL_MCP_CLIENT_ID` / client id list |
| MCP **401** with JWT | Wrong `EDA_COGNITO_CLIENT_ID`, expired token, or `MCP_SERVICE_TOKEN` set alongside OAuth |
| MCP **406** on `/mcp` | Missing `Accept: application/json, text/event-stream` |
| MCP **400** “Missing session ID” | `tools/list` before `initialize` or missing `Mcp-Session-Id` |
| Cognito **redirect_mismatch** | Callback URL not allowlisted on MCP app client |

---

## Order of execution (summary)

```text
Phase 0  →  pytest + optional docker build
Phase 1  →  REST API (key + JWT) against EDA_API_BASE
Phase 2  →  MCP local + validate_mcp_sandbox.sh + optional pytest integration
Phase 3  →  MCP Docker repeat
Phase 4  →  tools/call + log correlation (recommended)
Phase 5  →  public HTTPS + Claude-specific Cognito gates → then Claude
```

After Phase **5**, proceed to [claude-getting-started.md](claude-getting-started.md) for connector setup.
