# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Multi-channel uploads.** `start_upload` now returns every byte channel the API offers (`channels` + `fallback`) and an ordered runbook in `next_steps`. File contents still never pass through tool arguments or the conversation on any channel.
- **`upload_from_url` tool** — hands EasyDeploy a URL to fetch an upload session's bytes from, server-side: Google Sheets and Drive share links set to anyone-with-link, OpenAI file links, and other allowlisted hosts. For sandboxes with no network egress.
- **`get_upload_status` tool** — polls an upload session (`URL_ISSUED`, `RECEIVING`, `UPLOADED`, `VALIDATING`, `READY`, `REJECTED`, `CONSUMED`, `EXPIRED`) and adds `next_steps` guidance derived from the state.
- **`openai/fileParams` on `start_upload`** — an optional `file` parameter (`download_url`, `file_id`, `mime_type`, `file_name`) declared via tool `_meta`, so Codex and ChatGPT can hand a sandbox file straight to EasyDeploy. When present, `start_upload` calls from-url itself and returns the session in `RECEIVING`; the download URL is never echoed back to the model.
- **`api_client.upload_from_url`** (`POST /uploads/from-url`, 30 s) and **`api_client.get_upload_status`** (`GET /uploads/{id}`, 15 s). Both surface the API's own error message text unchanged.
- **Model-builder fallback documented in the tool descriptions and errors**: when a host offers no byte channel, the agent hands the file to the user as a download, points at the model builder (for example <https://www.easydeploy.ai/model-builder>), then resolves the resulting dataset by the name or URL the user reports back rather than guessing. The link comes from the `fallback` string `start_upload` returns (see Changed).
- **`update_dataset` tool** — renames a dataset or changes its description (`name` and/or `description`, at least one required). Replaces the write path that used to hide inside `get_dataset`.
- **Tool annotations on every tool** (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) via FastMCP's `annotations=`. The 16 `get_*`/`list_*` tools are read-only and idempotent; every write is non-destructive; `update_dataset` is the only idempotent write; `upload_from_url` and `start_upload` (which fetches a host `file` URL) are the open-world tools. `dataset_type`, `version_type` and `qa_status` are `Literal` types, so their schemas list enums. `create_project` and `create_model` are create-or-update and marked non-idempotent. `start_upload` keeps its `openai/fileParams` meta.
- **`get_model_report` documents validation and the `metrics` object.** The description now states how models are validated (10-fold CV — stratified shuffled on ROC-AUC for classifiers, shuffled on negative MSE for regressors, forward-chaining `TimeSeriesSplit` in time-series mode; winner refit on all rows; no separate test set; accuracy, confusion matrix, per-class precision/recall and in-sample ROC-AUC are training fit; the CV score is the out-of-sample estimate; the prose summary is LLM-written) and the shape of the structured `metrics` object (`taskType`, `crossValidation`, `trainingFit`, `additionalCv`, `warnings`, or `null` with `metricsUnavailableReason`). The MCP layer passes it through untouched, whether the API puts it in `data` or `meta`.
- `get_training_status(wait=true)` adds `next_steps` when it times out: the job is still running, call again with the same `job_id`, do not resubmit.
- **`get_started` tool: the data-science playbook.** A read-only tool, listed first, that returns the EasyDeploy playbook for agents: the roles (the agent is the data scientist; EasyDeploy trains, deploys and predicts), the workflow with the tools at each step, and the hard rules (no leakage, split before any balancing, an honest test file, confirm data changes with the user, never mock data, never paste file contents into a tool). `section` selects `overview` (default), `prepare`, `split`, `upload`, `train`, `validate`, `predict` or `all`; the result carries `guide_version`, `updated`, the section list with summaries and `next`. Hosts cache tool descriptions and server instructions but fetch tool results live, so the tool's name and description are a fixed trigger and the content, shipped as Markdown package data in `easydeploy_ai_mcp/guide/`, changes with each release.
- **Server instructions.** The server sends MCP `instructions` in its initialize result (`FastMCP(..., instructions=...)`). They open with "Call get_started before any other EasyDeploy tool" and keep a short core for hosts that show them: the roles, no leakage, split train and test yourself and train on train only, judge the model by scoring the test file with `run_batch_prediction`, confirm data changes, never mock data, never paste file contents into a tool.

### Changed

- **Tool wording follow-up for backend PR #88** (wording only, no behaviour change; playbook 1.2):
  - `complete_upload`: a retried call on a session that is already `CONSUMED` returns the existing dataset version instead of an error; a 409 means another `complete_upload` for the same session is still in progress, so wait a few seconds and retry.
  - `get_upload_status`: states that it covers sessions opened by `start_upload` (and fed by `upload_from_url` or a host file), and that files uploaded through the web model builder are not visible through it — the user registers those in the web app.
  - `create_dataset_version`: `file_url` must point to a file under the caller's own storage prefix `users/{userId}/`; the API rejects any other location with 403.
  - Model-builder fallback: `start_upload`'s runbook and description, `upload_from_url`, the module docs, the `upload` playbook section, README and the Claude getting-started guide tell agents to use the `fallback` string from the `start_upload` result, which carries the API's stage-aware model-builder URL, and cite www.easydeploy.ai/model-builder only as an example.

- Playbook 1.1: agents get raw data into their own environment (download a share link, read an attachment, or ask the user to attach it) and prepare and split it before any upload. `upload_from_url` is described as the channel for prepared files only. Tool descriptions and server instructions are unchanged, so hosts pick this up without a refresh.

- The playbook and `run_batch_prediction` say ids belong out of the training file but can stay in test and scoring files, since prediction ignores columns the model wasn't trained on and returns them in the output.

- **`complete_upload` requires upload status `READY`**, not `UPLOADED` — the validator promotes the bytes before a dataset version is created. Other states return a 400 naming the state; poll `get_upload_status` first. Documented in the tool description.
- Tool catalog is now **28 tools** (was 24): `get_started`, `upload_from_url`, `get_upload_status` and `update_dataset` are new.
- **`get_dataset` is read-only.** Its `name` and `description` parameters are gone; use `update_dataset`.
- **`complete_upload` output** returns the dataset version once (top-level `datasetVersion` with its `ui_url`); the duplicate nested `dataset.datasetVersion` is dropped.
- **`columnNames` lists instead of `columnNamesJson` strings** in `complete_upload`, `get_dataset`, `get_dataset_version`, `create_dataset_version` and `list_dataset_versions` output. A value that does not parse to a list stays under `columnNamesJson` unchanged.
- **`start_upload` returns `dataset_id` only when the caller passed one**, so the API's pre-assigned id for a new dataset is no longer mistaken for an existing dataset. Docstrings spell out the rule: omit `dataset_id` to create a new dataset; pass an existing one to add a version (the `complete_upload` name is then ignored).
- Dataset-version docstrings state what the labels do: `qa_status` is informational and does not gate anything. `version_type` matters in one case: `submit_training_job` with an explicit `dataset_version_id` rejects a `raw` version. Omit `dataset_version_id` to train on the model version's own dataset. `submit_training_job` says the same. The "used by the QA pipeline" wording is gone.
- **Descriptions say who splits the data.** `complete_upload` explains that `train`, `test` and `validation` are files the agent produces by splitting the data itself; EasyDeploy stores the type as a label and never splits. `create_model_version` says to pass the train dataset version, never the test one. `start_upload` notes that train and test are separate uploads. `run_batch_prediction` describes holdout validation as the backend actually behaves: the downloaded CSV is the input file in its original row order with every column kept, the target included, plus `prediction` and `probability_<class>` columns, and the agent computes the holdout metrics itself. `get_model_report` says the CV score is measured inside the training file and the holdout estimate comes from scoring the test file, not from the report.

### Fixed

- **Time-series wording.** `create_model_version` now says precisely what `time_series_mode` does — forward-chaining cross-validation (`TimeSeriesSplit`), each fold training on earlier periods and scoring on later ones — and points at `metrics.crossValidation.strategy` in `get_model_report`, which records the strategy a run used (`time_series_split`).
- Removed stale-host debugging notes from the `submit_training_job` and `get_training_status` descriptions and the module docstring ("some hosts omit that tool…", "standard catalog is 26 tools").
- **`upload_from_url` allowlist wording.** The description no longer claims the source allowlist includes EasyDeploy's own upload hostname. It does not: the default list is OpenAI file storage and Google Drive/Sheets.
- **From-URL size cap.** `upload_from_url`, the README and the Claude guide state the fetch cap as 256 MB.

## [0.2.0] - 2026-09-23

The HTTP transport gains OAuth: MCP clients sign in through Cognito instead of
carrying a shared token, and a brokered redirect lets clients Cognito cannot
allowlist — ChatGPT connectors, Claude Code, Cursor, MCP Inspector — sign in at
all. stdio users are unaffected beyond the `caller_channel` fix.

### Security

- `MCP_SERVICE_TOKEN` comparison uses `hmac.compare_digest` (constant-time).
- Explicit `verify=True` on all outbound `httpx` clients.
- HTTPS enforced on `authorization_endpoint` and `token_endpoint` URLs from OIDC discovery before use.
- `EDA_MCP_OAUTH_ISSUER` override validated as HTTPS.
- `assert` guards replaced with proper runtime checks (asserts are disabled under `python -O`).
- **Brokered OAuth requires PKCE.** In broker mode `/authorize` refuses a request without `code_challenge_method=S256`. Cognito treats PKCE as optional and the broker's `/token` rewrite removes Cognito's own `redirect_uri` check, so a flow without it would leave a leaked code redeemable by anyone.
- **Broker redirect policy matches hosts exactly and pins known callback paths.** Subdomains of vendor hosts are no longer implied (`EDA_MCP_EXTRA_REDIRECT_HOSTS` takes `.host` to opt in), `openai.com` is narrowed to `chat.openai.com/aip/`, and URIs with dot segments, backslashes, whitespace or non-ASCII are refused so the policy and the browser cannot disagree about where a redirect goes.
- The server logs a warning when brokering without `EDA_MCP_BROKER_SECRET`; the fallback key is derived from public identifiers.

### Added

- **OAuth 2.0 resource-server mode** for the HTTP transport. Set `EDA_OAUTH_ENABLED=1` with `EDA_COGNITO_USER_POOL_ID` and `EDA_COGNITO_CLIENT_ID` to validate incoming `Authorization: Bearer <jwt>` headers locally against the Cognito JWKS and forward the user's token to the EasyDeploy API. EasyDeploy API keys (`eda_live_*`) are accepted in the same header and forwarded verbatim.
- **`/.well-known/oauth-protected-resource`** (RFC 9728) published in OAuth mode so MCP clients can discover the authorization server from a 401. Unauthorized responses include a `WWW-Authenticate: Bearer …` header.
- **OAuth AS metadata proxy** (`/.well-known/oauth-authorization-server`) at the MCP origin. Proxies `authorization_endpoint` and `token_endpoint` from Cognito's OIDC discovery, stripping RFC 8707 `resource` params that Cognito rejects.
- **Static DCR** (`POST /oauth/register`, `POST /register`) returns the pre-configured Cognito public client id (RFC 7591-style).
- **`/authorize`** and **`/token`** proxy endpoints for MCP OAuth broker compatibility.
- **`scripts/run_mcp_docker_local.sh`** — build the Dockerfile and run the HTTP MCP locally.
- **Brokered authorization** (`EDA_MCP_OAUTH_BROKER=1`, default off). `/authorize` sends Cognito the server's own `{issuer}/oauth/callback` and seals the client's `redirect_uri` into the OAuth `state`; the new `GET /oauth/callback` forwards the authorization code to that URI with the client's own `state` restored, and `/token` rewrites `redirect_uri` to match. This is what lets clients Cognito cannot allowlist sign in at all — ChatGPT's per-connector `https://chatgpt.com/connector/oauth/<callback_id>`, and the runtime loopback ports Claude Code, Cursor and MCP Inspector bind. Register `{issuer}/oauth/callback` on the Cognito app client before turning it on. See [Brokered authorization](README.md#brokered-authorization).
- **`oauth_broker` redirect policy** — brokering moves the `redirect_uri` check off Cognito, so the server enforces its own: HTTPS on a known vendor host or subdomain, RFC 8252 §7.3 loopback, and a short private-use scheme allowlist. Extend with `EDA_MCP_EXTRA_REDIRECT_HOSTS`, `EDA_MCP_EXTRA_REDIRECT_SCHEMES`, `EDA_MCP_ALLOW_LOOPBACK_REDIRECT`; seal the state with `EDA_MCP_BROKER_SECRET`.

### Fixed

- **Browser CORS beyond Claude:** the allowlist now covers ChatGPT (`chatgpt.com`, `chat.openai.com`), VS Code Web and Insiders, Cursor, and a locally run MCP Inspector, alongside the Claude origins. A browser-hosted client whose origin was missing never got to send the request — the preflight failed and the assistant reported that it could not reach the server. Add more with `EDA_CORS_EXTRA_ORIGINS`.
- **MCP OAuth discovery:** RFC 9728 resource metadata lists the MCP host in `authorization_servers`; the AS metadata proxy ensures every client hits the local endpoints rather than Cognito directly (Cognito's pool issuer does not serve `oauth-authorization-server`).
- **`api_client`:** REST helpers now consistently accept and forward `caller_channel` to `X-Caller-Channel`.

### Changed

- `MCP_SERVICE_TOKEN` and `EDA_OAUTH_ENABLED` are mutually exclusive — setting both raises at import time.
- In OAuth mode, outbound EasyDeploy API calls use only the per-request bearer; `EDA_API_KEY` is not used as a fallback.
- `EDA_API_BASE` is optional; the client defaults to the production EasyDeploy API. Set it only for custom or staging endpoints.

## [0.1.0] - 2026-04-07

### Added

- Initial public release of the **EasyDeploy AI** MCP server as installable package `easydeploy-ai-mcp`.
- **stdio** entrypoint (`easydeploy-ai-mcp-stdio`, `python -m easydeploy_ai_mcp`) and **HTTP** entrypoint (`easydeploy-ai-mcp-http`, ASGI `easydeploy_ai_mcp.http_main:app`) with Streamable MCP on `/mcp` and `GET /healthz`.
- 24 tools covering EasyDeploy public REST operations: projects, datasets, uploads, models, training, predictions, account.
- Optional `MCP_SERVICE_TOKEN` for gating the HTTP MCP surface; HTTPS-only calls to the EasyDeploy API.
- `Dockerfile` for self-hosted deployments.

[Unreleased]: https://github.com/easydeploy-ai/easydeploy-ai-mcp/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/easydeploy-ai/easydeploy-ai-mcp/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/easydeploy-ai/easydeploy-ai-mcp/releases/tag/v0.1.0
