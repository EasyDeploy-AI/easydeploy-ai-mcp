# Validate MCP against sandbox public API

Use this checklist to prove the **HTTP MCP** image, **OAuth** (or legacy) env, and **tool → API** path against the EasyDeploy **sandbox** API Gateway before rolling out beta/prod MCP hosts.

For the **full** order of operations (pytest, REST-only checks, Docker, pre–Claude gates), see [e2e-mcp-pre-claude-validation-plan.md](e2e-mcp-pre-claude-validation-plan.md).

## Run the MCP HTTP server

**Docker** is what you **deploy** (same image as [Dockerfile](../Dockerfile): `easydeploy-ai-mcp[oauth]`). Use it for sandbox smoke tests when you want **parity with production**.

**venv / conda** is optional for **local** runs (`easydeploy-ai-mcp-http`) without building an image.

### Docker (local or deploy parity)

**Quickest:** from repo root, copy [`.env.example`](../.env.example) to **`.env`** (gitignored). `run_mcp_docker_local.sh` reads **only** `.env`, not `.env.example`. Avoid inline `# …` on `KEY=value` lines in `.env` (Docker can include the comment in the value). Then:

```bash
./scripts/run_mcp_docker_local.sh
```

The script **`docker build`s** [`Dockerfile`](../Dockerfile) as **`easydeploy-ai-mcp:local`** and **`docker run`s** with `-p 8080:8080`, passing **`--env-file .env`** when `.env` exists. Override the image tag with **`EDA_MCP_IMAGE`**, host port with **`PORT`**. Extra args go to `docker run` (e.g. `-e EDA_API_KEY=…` if you skip `.env`).

**Manual** equivalent:

```bash
docker build -t eda-mcp-local .
docker run --rm -p 8080:8080 \
  -e EDA_API_BASE="https://YOUR_API_ID.execute-api.us-east-1.amazonaws.com/prod" \
  -e EDA_OAUTH_ENABLED=1 \
  -e EDA_COGNITO_USER_POOL_ID="us-east-1_XXXXXXXXX" \
  -e EDA_COGNITO_CLIENT_ID="your-cognito-client-id" \
  -e EDA_COGNITO_REGION="us-east-1" \
  eda-mcp-local
```

**Legacy** (no OAuth): omit `EDA_OAUTH_ENABLED` and Cognito vars; add `-e EDA_API_KEY="eda_live_…"` (and optionally `-e MCP_SERVICE_TOKEN=…`).

Then `export MCP_SMOKE_BASE_URL=http://127.0.0.1:8080` (or `${PORT}`) and run `./scripts/validate_mcp_sandbox.sh` from a **second** terminal (scripts only need `curl`, not Python).

### Python environment (venv or conda) — local without Docker

Use an isolated env so `pip install` does not touch system Python.

**venv** (from repo root `easydeploy-ai-mcp/`):

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -U pip
pip install -e ".[dev]"            # pytest + pyjwt; use -e ".[oauth]" if you only need HTTP + Cognito
```

**conda**:

```bash
conda create -n eda-mcp python=3.12 -y
conda activate eda-mcp
pip install -U pip
pip install -e ".[dev]"
```

Keep the env **activated** in the same terminal while you run `easydeploy-ai-mcp-http`, `pytest`, and `./scripts/validate_mcp_sandbox.sh`.

## Sandbox API base

Outbound REST calls use [`normalize_api_base`](../src/easydeploy_ai_mcp/api_client.py): the value is normalized to `…/v1`.

```bash
export EDA_API_BASE="https://YOUR_API_ID.execute-api.us-east-1.amazonaws.com/prod"
```

Effective upstream prefix:

`https://YOUR_API_ID.execute-api.us-east-1.amazonaws.com/prod/v1/…`

## OAuth mode (multi-tenant HTTP)

From the **same** Amplify sandbox stack that owns this API (CloudFormation **Outputs**):

| Variable | Value (EasyDeploy sandbox example) |
|----------|--------|
| `EDA_OAUTH_ENABLED` | `1` |
| `EDA_COGNITO_USER_POOL_ID` | `us-east-1_XXXXXXXXX` (confirm in stack outputs) |
| `EDA_COGNITO_CLIENT_ID` | `your-cognito-client-id` (`McpClaudeOauthUserPoolClientId`) |
| `EDA_COGNITO_REGION` | `us-east-1` |

Do **not** set `MCP_SERVICE_TOKEN` with OAuth enabled.

Install **`easydeploy-ai-mcp[oauth]`** (included in the repo **Dockerfile**).

**Outbound REST host:** the MCP process uses **`EDA_API_BASE`** for every tool (default **`https://api.easydeploy.ai`**). If that host does not resolve inside Docker or you use **sandbox** JWTs/API, set **`EDA_API_BASE`** in **`.env`** to your execute-api base (same value as `validate_mcp_sandbox.sh` / `smoke-mcp-http.mjs`). Otherwise `tools/call` can fail with **`[Errno -2] Name or service not known`** while direct API checks from your laptop still pass.

## Legacy mode (API key only, optional)

To validate **Docker/build** and **API** connectivity without Cognito:

- Leave `EDA_OAUTH_ENABLED` unset.
- Set `EDA_API_BASE` as above and `EDA_API_KEY`.
- Optionally set `MCP_SERVICE_TOKEN` to gate `/mcp`.

## API key only (no MCP)

From the repo root:

```bash
export EDA_API_KEY="eda_live_…"
./scripts/validate_sandbox_api_key.sh
```

Optional: `export EDA_API_BASE=…` if not using the default sandbox execute-api URL in the script.

## Automated smoke script

With the MCP server already listening (**Docker** or **venv** + `easydeploy-ai-mcp-http`), from the repo root:

```bash
export MCP_SMOKE_BASE_URL="http://127.0.0.1:8080"
export EDA_API_BASE="https://YOUR_API_ID.execute-api.us-east-1.amazonaws.com/prod"
# Optional — direct API check (step 4)
export EDA_SMOKE_API_KEY="eda_live_…"
# Or use the same variable as the MCP server:
# export EDA_API_KEY="eda_live_…"
# Optional — MCP POST /mcp (OAuth). Same token name as integration pytest is accepted:
# export EDA_SMOKE_ACCESS_TOKEN="eyJ…"   # or: export EDA_INTEGRATION_COGNITO_ACCESS_TOKEN="eyJ…"
# Obtain JWT with PKCE (from this repo):
#   python3 scripts/cognito_mcp_get_access_token.py \
#     --cognito-host "<prefix>.auth.us-east-1.amazoncognito.com" \
#     --client-id "$EDA_COGNITO_CLIENT_ID"
# (Prefer default *.amazoncognito.com if custom auth.sandbox.easydeploy.ai shows "Login pages unavailable".)
# (Host is Cognito custom domain or *.auth.*.amazoncognito.com — NOT execute-api URL.)

./scripts/validate_mcp_sandbox.sh
```

### Full MCP smoke (Node, `tools/call`)

[`scripts/smoke-mcp-http.mjs`](../scripts/smoke-mcp-http.mjs) mirrors the bash checks and adds **`tools/call`** (default `get_account_status`) so the server must call the EasyDeploy REST API with the same bearer. **Requires Node 18+.**

```bash
# Same env as validate_mcp_sandbox.sh; OAuth on MCP → set a JWT or API key bearer
export MCP_SMOKE_BASE_URL="http://127.0.0.1:8080"
export EDA_SMOKE_ACCESS_TOKEN="eyJ…"   # or EDA_API_KEY for legacy / API-key OAuth
node scripts/smoke-mcp-http.mjs

# Optional: another tool + JSON args
node scripts/smoke-mcp-http.mjs --tool list_projects --tool-args "{}"
```

### Full train + predict (HTTP MCP)

[`scripts/smoke-mcp-train-predict.mjs`](../scripts/smoke-mcp-train-predict.mjs) runs the full **upload → dataset → model version → training → ad-hoc prediction** path, but each step is **`tools/call`** on `/mcp` plus a **gateway `PUT`** for the CSV (same as `start_upload`’s `curl_command`).

```bash
export MCP_SMOKE_BASE_URL=http://127.0.0.1:8080
export EDA_SMOKE_ACCESS_TOKEN="eyJ…"   # or EDA_API_KEY when allowed
node scripts/smoke-mcp-train-predict.mjs --file /path/to/Breast_Cancer_Wisconcin_ds.csv
# Optional: --target diagnosis --project-name "QA MCP" --training-timeout-min 45
# Dev only: --no-poll (skips waiting; prediction may fail if training is not done)
```

Shared helpers live in [`scripts/mcp-streamable-client.mjs`](../scripts/mcp-streamable-client.mjs). Training typically takes several minutes; the script polls **`get_model_version`** until **`TRAINING_COMPLETED`**.

## Manual steps (same as script)

1. Start MCP: **Docker** (see above) or activate **venv** / **conda**, set **OAuth** or **legacy** env and `EDA_API_BASE`, then `easydeploy-ai-mcp-http`.
2. `GET /healthz` → `{"status":"ok"}`.
3. If OAuth: `GET /.well-known/oauth-protected-resource` → JSON with `authorization_servers` = MCP origin; `GET /.well-known/oauth-authorization-server` → **200** JSON (proxy AS metadata; endpoints point at Cognito).
4. Direct API: `curl -sS -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $EDA_API_KEY" "$EDA_API_BASE/v1/account"` → `200` (after normalization, `EDA_API_BASE` may already include `/v1`; if you export the raw `…/prod` value, use `…/prod/v1/account`).
5. MCP (Streamable HTTP): `POST /mcp` with `Accept: application/json, text/event-stream`, `Authorization: Bearer <Cognito access token>` (MCP app client). Run `initialize`, read the `mcp-session-id` response header, then `tools/list` (or `tools/call` for e.g. `get_account_status`) on the same session. Confirm outbound REST calls hit the sandbox host (API logs). The script automates initialize + `tools/list` when `EDA_SMOKE_ACCESS_TOKEN` is set.

## After sandbox passes

- Point **beta/prod** MCP at **their** API URLs and **their** stack `McpClaudeOauthUserPoolClientId` outputs.
- When clean API domains exist, update only `EDA_API_BASE`.

### PKCE script troubleshooting

| Symptom | What to do |
|---------|------------|
| **`SSL: CERTIFICATE_VERIFY_FAILED`** on token exchange (after browser login) | macOS **python.org** Python: run **Install Certificates.command** in the Python folder; or `pip install certifi` (included in `.[dev]`); or dev-only `python3 scripts/cognito_mcp_get_access_token.py --insecure-ssl …` |
| **Custom domain** shows **Login pages unavailable** | Cognito Hosted UI / custom domain not fully configured for that pool (check Cognito console → App integration → Domain). Prefer the default **`*.auth.<region>.amazoncognito.com`** host for local PKCE until the custom domain works. |
| Browser opened **`/oauth2/authorize`** but your product only documents **`/login`** | Cognito’s OAuth entry is normally **`/oauth2/authorize`**. If your routing requires another path, try `--authorize-path login` (only if your IdP documents it). |

See also [aws-p0.md](aws-p0.md) for deployment and compliance guidance.
