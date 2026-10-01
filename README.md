# EasyDeploy AI MCP (`easydeploy-ai-mcp`)

A [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server that exposes the **[EasyDeploy](https://easydeploy.ai)** public REST API as tools for Claude, Cursor, Claude Code, and other MCP clients.

**PyPI package name:** `easydeploy-ai-mcp` · **Import package:** `easydeploy_ai_mcp`

## Contents

- [Connect with Claude](#connect-with-claude)
- [What you get](#what-you-get)
- [Requirements](#requirements)
- [Install](#install)
- [Environment variables](#environment-variables)
- [Local MCP (stdio)](#local-mcp-stdio)
- [Remote MCP (HTTP)](#remote-mcp-http)
- [Documentation](#documentation)
- [REST API reference](#rest-api-reference)
- [Releases and API compatibility](#releases-and-api-compatibility)
- [Development](#development)
- [Security](#security)
- [License](#license)

## Connect with Claude

### Hosted connector ([claude.ai](https://claude.ai) or Claude Desktop)

We host the MCP endpoint. You add it once inside Claude; after you connect and sign in, new chats can use EasyDeploy like any other enabled connector.

1. Open Claude in the browser or open **Claude Desktop**.
2. Open **Settings**, then **Connectors**, and choose **Add custom connector**.
3. Enter exactly:
   - **Name:** `EasyDeploy AI`
   - **Remote MCP server URL:** `https://mcp.easydeploy.ai/mcp`
4. Save the connector so EasyDeploy appears in your list of connectors.
5. Open the EasyDeploy connector entry and choose **Connect**, then finish sign-in in your browser. That step authorizes Claude to use your EasyDeploy account.

**Connect and sign in (inside Claude):** After the custom connector is set up, Claude shows an EasyDeploy card with the MCP URL and a **Connect** button. Use that flow to sign in. You do not paste an API key into Claude. Access stays tied to the EasyDeploy profile you authenticate in the browser.

**Claude Desktop and file uploads:** File uploads and some tool calls reach EasyDeploy over the network. On Desktop, Claude blocks outbound traffic unless you allow the domains it should call. Open **Settings → Capabilities**, turn on **Allow network egress**, and under the domain allowlist add this one host exactly:

`api.easydeploy.ai`

That is the only host uploads to the public production server go to. Do not use a wildcard such as `*.execute-api.us-east-1.amazonaws.com`: it would also allow every other API Gateway in that region, which is an open channel out of the sandbox. A server pointed at another EasyDeploy API (the beta server, or one set with `EDA_API_BASE`) needs that API's host instead, and the `start_upload` runbook names the exact host to allow.

> The domain allowlist UI is available on paid Claude plans.

More detail and variants (for example a URL from your own deployment) are in [docs/claude-getting-started.md](docs/claude-getting-started.md).

### Start here: `get_started`

`get_started` returns the EasyDeploy data-science playbook, and the server's MCP `instructions` tell the agent to call it before any other EasyDeploy tool and again at the start of every new modeling task. Hosts cache tool descriptions and server instructions, and many never show the instructions to the model; a tool result is fetched live on every call. So the playbook lives in the tool's result, not in its description: the name and description stay fixed, and the content is updated with each release without anyone reconnecting.

- `get_started()` returns the overview: the roles, the whole workflow with the tools at each step, and the hard rules.
- `get_started(section=...)` returns one section: `prepare`, `split`, `upload`, `train`, `validate` or `predict`. `all` returns every section.
- Each result carries `guide_version`, `updated`, the list of sections with a one-line summary each, and `next`, the section to read next.

The content ships inside the package as Markdown, one file per section, in [`src/easydeploy_ai_mcp/guide/`](src/easydeploy_ai_mcp/guide/). The tool is read-only and makes no API call.

### You are the data scientist

EasyDeploy searches models, engineers features, tunes hyperparameters, trains, deploys and predicts. It does not clean your data, define the target, split it, or check the model on data it never saw. The agent does that, following the `get_started` playbook:

1. **Prepare.** Agree the decision and the target with you, explore the data, drop identifiers and anything not known at prediction time from the training file, and write a one-sentence target definition. Ids can stay in test and scoring files: prediction ignores columns the model wasn't trained on and returns them in the output, which makes joining results back easy. Data changes and dropped columns are confirmed with you before they are applied.
2. **Split.** Stratified 80/20 for classification, chronological for time series, no entity in both files, no duplicates. Balancing such as SMOTE touches the train file only.
3. **Upload and train.** The files go up separately, with `dataset_type` `train` and `test` (`validation` optional). The model version is created on the train dataset version only.
4. **Validate on the holdout.** The report's cross-validation score is measured inside the training file and its training-fit metrics are in-sample. The real estimate comes from `run_batch_prediction` on the test dataset version: the downloaded CSV keeps every input column, the true label included, in the original row order and adds `prediction` and `probability_<class>` columns. The agent computes the metrics from it and picks the decision threshold from the cost of each kind of error (best F1 only when those costs are unknown), or reports the error margin for regressors.
5. **Use real outputs.** Dashboards, reports and scored lists are built from real predictions, never mock values.

### Getting a training file into EasyDeploy

`start_upload` opens an upload session and the agent picks the best byte channel the host offers. **File contents never travel through the conversation or through tool arguments on any channel** — the agent passes a URL or runs a `curl`, never the data itself.

| Channel | When it is used | Flow |
| --- | --- | --- |
| **File parameter** | Hosts that support `openai/fileParams` (Codex, ChatGPT). `start_upload` declares `_meta: {"openai/fileParams": ["file"]}`, so the host hydrates a `file` argument with a download URL. No sandbox network needed. | `start_upload(..., file=…)` → session goes to `RECEIVING` → poll `get_upload_status` → `complete_upload` |
| **Gateway PUT** (default) | Claude Code, Cowork, and claude.ai / Desktop sandboxes with egress. Max **6 MB** per file; the gateway answers **413** above that, and there is no chunking path. | `start_upload` → run the returned `curl_command` in bash → poll `get_upload_status` → `complete_upload` |
| **Fetch from a share link** | The sandbox has no egress but the file is already at a shareable URL: a Google Sheets or Drive link set to **anyone with the link**, an OpenAI file link, or another allowlisted host. EasyDeploy fetches the bytes server-side, up to **256 MB**. | `start_upload` → `upload_from_url(upload_request_id, source_url)` → poll `get_upload_status` → `complete_upload` |

`get_upload_status` reports `URL_ISSUED → RECEIVING → UPLOADED → VALIDATING → READY | REJECTED → CONSUMED` (plus `EXPIRED`). **`complete_upload` requires `READY`** — every byte that becomes a dataset version passes the CSV validator first, and a session still in flight, `REJECTED` or `EXPIRED` returns a 400 naming what to do. On `REJECTED`, the `error` field says which check failed. Retrying `complete_upload` is safe: on a session that is already `CONSUMED` it returns the existing dataset version, and a 409 means another call for the same session is still in progress (wait a few seconds and retry). `get_upload_status` only covers sessions opened by `start_upload`; files uploaded through the web model builder are registered in the web app and do not appear there.

**New dataset or new version.** Omit `dataset_id` on `start_upload` and `complete_upload` creates a new dataset with the name you give it. Pass an existing dataset's id and the upload becomes a new version of that dataset; `complete_upload`'s `name` is then ignored and the dataset keeps its name. `complete_upload` returns `dataset` and `datasetVersion` once each, with the column names as a `columnNames` list. `qa_status` is informational and does not gate anything. `version_type` matters in one case: `submit_training_job` with an explicit `dataset_version_id` rejects a `raw` version. Omit `dataset_version_id` to train on the model version's own dataset.

**Fallback when the host has no byte channel at all** (no egress and no file bridge): the agent saves the training file, hands it to you as a download, and points you at the web model builder using the `fallback` text `start_upload` returns, which carries the right model-builder URL for the environment (for example **[easydeploy.ai/model-builder](https://www.easydeploy.ai/model-builder)**). After you upload there, tell the agent the **dataset name you entered** or paste the **dataset URL** from the page — it resolves the dataset with `list_datasets` and confirms the row count before continuing. That upload is already registered as a dataset, so `complete_upload` is not used for it. Adding the allowlist entry above (where your plan allows it) avoids the detour.

### Reading a model report

`get_model_report` returns the report plus a structured `metrics` object (passed through unchanged from the API). Model search scores candidates with 10-fold cross-validation (stratified and shuffled on ROC-AUC for classifiers, shuffled on negative MSE for regressors; forward-chaining `TimeSeriesSplit` for a version created with `time_series_mode=true`), then refits the winner on all rows. There is no separate test set: accuracy, the confusion matrix, per-class precision/recall and in-sample ROC-AUC under `metrics.trainingFit` are training fit, and `metrics.crossValidation.score` is the report's out-of-sample estimate, measured inside the training file. The holdout estimate comes from scoring your own test file with `run_batch_prediction`, not from the report. `metrics.crossValidation.strategy` records which CV a run used (`time_series_split` for forward-chaining), and `metrics.warnings` flags anything to be careful quoting. The prose summary is written by an LLM and may use looser wording.

---

### Local MCP on your computer (stdio)

Run the MCP server on your own machine using your EasyDeploy API key. Nothing is exposed to the internet.

**1. Install**

```bash
pip install easydeploy-ai-mcp
```

**2. Add to Claude Desktop config**

Edit (or create) the Claude Desktop config file:

| OS      | Path |
| ------- | ---- |
| macOS   | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

Merge the following into the root of that JSON (keep any existing keys):

```json
{
  "mcpServers": {
    "EasyDeploy AI": {
      "command": "easydeploy-ai-mcp-stdio",
      "env": {
        "EDA_API_KEY": "eda_live_YOUR_KEY"
      }
    }
  }
}
```

Replace `eda_live_YOUR_KEY` with your key from **Account → API Keys** in the EasyDeploy dashboard. Use the full path to `easydeploy-ai-mcp-stdio` (run `which easydeploy-ai-mcp-stdio` to find it) if Claude cannot locate it on your `PATH`.

**3. Restart Claude Desktop**

Fully quit and reopen the app. **EasyDeploy AI** will appear in your MCP servers.

---

For self-hosting on Docker or a cloud provider, see [Remote MCP (HTTP)](#remote-mcp-http).

## What you get

- **28 tools**: `get_started` (the playbook) plus tools covering projects, datasets (including the three upload channels), model versions, training jobs, predictions, and account status.
- **Tool annotations** on every tool (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`), so hosts can tell reads from writes. `get_started` and the 16 other `get_*` / `list_*` tools are read-only; no tool deletes anything; only `upload_from_url` and `start_upload` (given a host `file`) make EasyDeploy fetch an external URL. `create_project` and `create_model` are create-or-update (they PATCH when an id is passed), so they are marked as non-idempotent writes. Renaming a dataset is its own tool, `update_dataset`; `get_dataset` is a pure read.
- **stdio** transport for local clients, or **HTTP** with Streamable MCP on `/mcp` and **GET /healthz** for load balancers.
- **Hardening:** HTTPS-only calls to the EasyDeploy API; optional `MCP_SERVICE_TOKEN` for the HTTP MCP surface; response fields trimmed where appropriate for agents.

For **production** and **SOC 2–sensitive** setups, prefer self-hosting so your data stays within your own infrastructure. The hosted connector at `https://mcp.easydeploy.ai/mcp` is fine for most users.

## Requirements

- Python **3.10+**
- An EasyDeploy **API key** from the dashboard (**Account → API Keys**). The client uses the production EasyDeploy API host by default.

## Install

### From PyPI (after first release)

```bash
pip install easydeploy-ai-mcp
```

### From source

```bash
git clone https://github.com/easydeploy-ai/easydeploy-ai-mcp.git
cd easydeploy-ai-mcp
pip install -e ".[dev]"   # includes pytest
# or minimal runtime only:
pip install .
```

## Environment variables


| Variable                           | Required | Description                                                                                     |
| ---------------------------------- | -------- | ----------------------------------------------------------------------------------------------- |
| `EDA_API_KEY`                      | stdio / legacy HTTP | Required for **stdio** and **legacy** HTTP (no OAuth). **Not** used for outbound API calls when `EDA_OAUTH_ENABLED=1` — each MCP request must include `Authorization: Bearer <JWT or eda_live_…>`. |
| `EDA_API_BASE`                     | No       | Overrides the default production API (`https://api.easydeploy.ai`). Set only when targeting a non-production endpoint. Trailing `/v1` is optional. |
| `EDA_UI_BASE_URL`                  | No       | Prefix for `ui_url` fields (default `https://easydeploy.ai`).                                   |
| `MCP_SERVICE_TOKEN`                | No       | Legacy single-tenant gate. If set, HTTP mode requires `Authorization: Bearer <token>` for `/mcp` (not for `GET /healthz`). Mutually exclusive with `EDA_OAUTH_ENABLED`. |
| `EDA_OAUTH_ENABLED`                | No       | Set to `1` to run the HTTP transport as an OAuth 2.0 resource server. Requires `EDA_COGNITO_USER_POOL_ID` and `EDA_COGNITO_CLIENT_ID`. See [Remote MCP (HTTP)](#remote-mcp-http). |
| `EDA_COGNITO_USER_POOL_ID`         | OAuth    | Cognito user pool that issues access tokens for the EasyDeploy API.                             |
| `EDA_COGNITO_CLIENT_ID`            | OAuth    | App client ID expected in the access token's `client_id` claim.                                 |
| `EDA_COGNITO_REGION`               | No       | AWS region for the user pool (default `us-east-1`).                                             |
| `EDA_REPORT_MAX_WAIT_SECONDS`      | No       | `get_model_report` poll budget (default `300`).                                                 |
| `EDA_REPORT_POLL_INTERVAL_SECONDS` | No       | Poll interval in seconds (default `10`).                                                        |
| `HOST` / `PORT`                    | No       | HTTP bind (defaults `0.0.0.0` / `8080`).                                                        |
| `EDA_TRUST_FORWARDED_HEADERS`      | No       | Set to `1` behind ALB/reverse proxy so RFC 9728 `resource` uses `https` (trusts `X-Forwarded-Proto`). |
| `EDA_MCP_OAUTH_ISSUER`             | No       | Public MCP base URL (no path) for `authorization_servers` and proxy `/.well-known/oauth-authorization-server` **`issuer`**. Default: request origin. Use if `Host` / `X-Forwarded-Proto` are wrong behind a proxy. |
| `EDA_CORS_EXTRA_ORIGINS`           | No       | Comma-separated browser origins to allow in addition to the built-in list (Claude, ChatGPT, VS Code Web, Cursor, MCP Inspector). |
| `EDA_MCP_OAUTH_BROKER`             | No       | Set to `1` to broker the authorization redirect. Default off. See [Brokered authorization](#brokered-authorization). |
| `EDA_MCP_EXTRA_REDIRECT_HOSTS`     | No       | Broker mode: comma-separated extra HTTPS callbacks accepted as client `redirect_uri`. `host` (any path), `host/path` (exact), `host/path/` (prefix), `.host` (host and subdomains). |
| `EDA_MCP_EXTRA_REDIRECT_SCHEMES`   | No       | Broker mode: comma-separated extra private-use URI schemes (RFC 8252 §7.1) accepted as client `redirect_uri`. |
| `EDA_MCP_ALLOW_LOOPBACK_REDIRECT`  | No       | Broker mode: set to `0` to refuse `http://127.0.0.1:<port>` callbacks. Default `1` — desktop clients need them. |
| `EDA_MCP_BROKER_SECRET`            | No       | Broker mode: HMAC key that seals the OAuth `state`. Set it in any real deployment; the fallback is derived from the Cognito pool and client ids, which are public, and the server logs a warning without it. |


## Local MCP (stdio)

Use when the client **starts** the server as a subprocess (Claude Desktop, Cursor, etc.).

```bash
export EDA_API_KEY="eda_live_..."
easydeploy-ai-mcp-stdio
```

Or: `python -m easydeploy_ai_mcp`

Example config snippet:

```json
{
  "mcpServers": {
    "easydeploy-ai": {
      "command": "easydeploy-ai-mcp-stdio",
      "env": {
        "EDA_API_KEY": "eda_live_..."
      }
    }
  }
}
```

## Remote MCP (HTTP)

Serves **Streamable HTTP** via FastMCP on **`/mcp`** (confirm with your pinned **FastMCP 3.x** version). Health checks: **GET /healthz**.

```bash
export EDA_API_KEY="eda_live_..."
easydeploy-ai-mcp-http
```

Or: `uvicorn easydeploy_ai_mcp.http_main:app --host 0.0.0.0 --port 8080`

If you embed `mcp.http_app()` in another ASGI app, pass through **`lifespan`** from the FastMCP HTTP app ([FastMCP ASGI](https://gofastmcp.com/deployment/asgi)); `easydeploy_ai_mcp.http_main` already does this for uvicorn.

### Auth modes

Pick exactly one (setting both `EDA_OAUTH_ENABLED` and `MCP_SERVICE_TOKEN` raises at import):

- **OAuth 2.0 resource server** (multi-tenant): set `EDA_OAUTH_ENABLED=1` plus
  `EDA_COGNITO_USER_POOL_ID` and `EDA_COGNITO_CLIENT_ID`. Install the optional
  extra: `pip install easydeploy-ai-mcp[oauth]`. The server validates incoming
  Cognito **access** JWTs locally against the Cognito JWKS (issuer, signature,
  `exp`, `token_use=='access'`, `client_id`) and forwards the token to the
  EasyDeploy API. EasyDeploy API keys (prefix `eda_live_`) are accepted in the
  same `Authorization: Bearer` header and forwarded as-is — the API is the
  source of truth for revocation. RFC 9728 metadata is published at
  `/.well-known/oauth-protected-resource`; RFC 8414 proxy metadata includes
  **`registration_endpoint`**, and **`POST /oauth/register`** returns the static
  Cognito MCP **`EDA_COGNITO_CLIENT_ID`** (RFC 7591-style, public client). 401
  responses include `WWW-Authenticate: Bearer …` so MCP clients can discover the
  auth server.
  Note: Cognito access tokens carry `client_id`, **not** `aud`; do not configure
  an audience.
- **Shared-secret gate** (legacy single-tenant): set `MCP_SERVICE_TOKEN`. All
  outbound API calls use the static `EDA_API_KEY`.
- **No auth**: development only.

### Brokered authorization

Cognito matches `redirect_uri` **literally** against the app client's callback
list — no wildcards, no prefixes, no ports — and it checks at
`/oauth2/authorize`, *before* rendering the Hosted UI. A client whose callback
is not registered therefore never sees a sign-in page: the browser lands on
`redirect_mismatch`, which reads as "the connector could not load the login
page" rather than as a configuration problem.

That works for clients with one fixed callback and cannot work for the rest:

| Client | Callback | Registrable? |
| --- | --- | --- |
| Claude (web, Desktop) | `https://claude.ai/api/mcp/auth_callback` | yes |
| ChatGPT custom connector | `https://chatgpt.com/connector_platform_oauth_redirect`, or `https://chatgpt.com/connector/oauth/<callback_id>` when it cannot identify the issuer | the first only |
| VS Code / Insiders | `https://vscode.dev/redirect` | yes |
| Claude Code, Cursor, MCP Inspector | `http://127.0.0.1:<port chosen at runtime>/callback` | no |

Set `EDA_MCP_OAUTH_BROKER=1` and the server sends Cognito **its own** callback,
`{issuer}/oauth/callback`, which is one fixed URL you register once. It seals
the client's real `redirect_uri` into the OAuth `state`, and when Cognito comes
back it forwards the authorization code to that URI with the client's own
`state` restored. `/token` rewrites `redirect_uri` to match, because Cognito
checks it a second time at the token exchange.

**This moves the redirect check from Cognito to this server, so it stays a real
check.** Without one, anybody could start a flow here with a callback they
control and collect a signed-in user's authorization code. `oauth_broker.py`
is about as tight as the list it replaces: an HTTPS callback must match a known
client's host exactly and, where that client's callback path is known, the path
too (`claude.ai/api/mcp/auth_callback`, `chatgpt.com/connector/oauth/…`,
`vscode.dev/redirect`). Subdomains are never implied — one open redirect or
dangling CNAME under a vendor's domain would otherwise be a place to collect
codes. Loopback is accepted per RFC 8252 §7.3, and private-use schemes from a
short allowlist. Extend it with `EDA_MCP_EXTRA_REDIRECT_HOSTS` /
`EDA_MCP_EXTRA_REDIRECT_SCHEMES` rather than by widening the code.

**PKCE is mandatory in broker mode.** Cognito treats `code_challenge` as
optional, and the broker's `/token` rewrite removes the one check Cognito made
there, so a flow without PKCE would leave the code bound to nothing — anyone
who found it in a browser history or a proxy log could redeem it. `/authorize`
refuses a brokered request without `code_challenge_method=S256`. Every MCP
client already sends one.

**Rollout order matters.** Register `{issuer}/oauth/callback` on the Cognito app
client *first*, then set `EDA_MCP_OAUTH_BROKER=1`. Doing it the other way round
breaks the clients that work today, which is why the default is off.

**Docker — run locally** (same image you deploy to ECS/Fargate; includes `easydeploy-ai-mcp[oauth]`):

```bash
# Convenience: build + run (reads .env in the repo root if present)
./scripts/run_mcp_docker_local.sh

# Explicit env vars (no .env)
./scripts/run_mcp_docker_local.sh -e EDA_API_KEY="eda_live_..."

# Different host port
PORT=9000 ./scripts/run_mcp_docker_local.sh
```

**Host on AWS (Fargate + ALB):** build and push this repo’s **Dockerfile** to a container registry, then deploy behind an HTTPS load balancer. Set the env vars listed above on the task/container.

Manual equivalent:

```bash
docker build -t easydeploy-ai-mcp .
docker run --rm -p 8080:8080 \
  -e EDA_API_KEY="eda_live_..." \
  easydeploy-ai-mcp
```

## Documentation

- **[docs/claude-getting-started.md](docs/claude-getting-started.md)** — EasyDeploy + Claude: Connectors or local Desktop config JSON
- **[docs/claude.md](docs/claude.md)** — Claude Connectors vs Claude Code, transports, headers

## REST API reference

This MCP server is a thin client over the **EasyDeploy public REST API**. Endpoint behavior, request bodies, and response shapes are defined by **EasyDeploy** (dashboard, product help, and official API materials at [easydeploy.ai](https://easydeploy.ai)). This repo does not duplicate the full OpenAPI spec; it maps those operations to MCP tools.

## Releases and API compatibility

**This repository** is the open-source home of the EasyDeploy MCP server. New releases track the **EasyDeploy public REST API** as documented for customers (dashboard and official API materials). If the API adds or changes endpoints, expect corresponding updates here. Contributors should follow [CONTRIBUTING.md](CONTRIBUTING.md) when changing tools or client behavior.

## Development

```bash
pip install -e ".[dev]"
pytest
```

**Optional — real Cognito JWT against the HTTP app** (live JWKS, no mocks): set `EDA_INTEGRATION_COGNITO_ACCESS_TOKEN` plus the same `EDA_COGNITO_*` vars you use for OAuth mode, then run `pytest tests/test_cognito_jwt_integration.py -v`. See the docstring in that file.

See [CONTRIBUTING.md](CONTRIBUTING.md) for pull requests and reporting issues.

## Security

See [SECURITY.md](SECURITY.md) for vulnerability reporting and deployment notes.

## License

MIT — see [LICENSE](LICENSE).