"""
easydeploy_ai_mcp.server
EasyDeploy AI MCP server — REST API wrapper (FastMCP name: **EasyDeploy AI**).

Tool definitions follow the EasyDeploy public REST API; keep them aligned when the API surface changes.

Local stdio: run ``easydeploy-ai-mcp-stdio`` (or ``python -m easydeploy_ai_mcp``) with env
``EDA_API_KEY`` set (from the dashboard). Optional ``EDA_API_BASE`` overrides the production API host for internal use only.

Remote HTTP: run ``easydeploy-ai-mcp-http`` or uvicorn ``easydeploy_ai_mcp.http_main:app``; see README.

Security:
  - All API calls enforce HTTPS (TLS) — non-HTTPS URLs are rejected.
  - Response sanitization strips internal storage paths and auth fields.
  - MCP stdio transport is a local process pipe — never traverses a network.
  - HTTP transport: use TLS in production; optional ``MCP_SERVICE_TOKEN`` gates the MCP app (not ``/healthz``).

Upload paths — three channels feed one ingest pipeline. File bytes never pass
through the model or through tool arguments on any of them:

  A. Host file parameter (Codex, ChatGPT — hosts that support ``openai/fileParams``):
     call start_upload with ``file``; the host hydrates a download URL, the API
     fetches the bytes server-side, and the session goes to RECEIVING.
  B. Gateway PUT (Claude Code, Cowork, claude.ai / Desktop sandbox with egress):
     start_upload → run curl_command in bash (max ~6 MB) → poll get_upload_status
     until READY → complete_upload.
  C. Fetch from a share link (no sandbox egress, file already on a shareable host):
     start_upload → upload_from_url with the Google Sheets/Drive or host file link
     → poll get_upload_status until READY → complete_upload.

  Final fallback when the host has neither egress nor a file bridge: hand the
  training file to the user as a download, send them to
  https://www.easydeploy.ai/model-builder, then ask for the dataset name or URL
  and resolve it with list_datasets. That upload is already a dataset — do not
  call complete_upload for it.

Tool catalog (27 tools; every tool carries MCP annotations — readOnlyHint etc.):
  Account: get_account_status
  Projects: list_projects, get_project, create_project (pass project_id to update)
  Datasets: list_datasets, get_dataset (read-only), update_dataset, start_upload,
    upload_from_url, get_upload_status, complete_upload
  Dataset versions: list_dataset_versions (project_id optional), get_dataset_version,
    create_dataset_version (pass version_id to update qa_status)
  Models: create_model (pass model_id to update), get_model, create_model_version,
    list_models, list_model_versions, get_model_version,
    get_model_report (project_id optional — inferred from model_id)
  Training: submit_training_job, get_training_status
  Predictions: run_prediction, run_batch_prediction (project_id + target_feature auto-resolved),
    get_prediction (includes batch download URL when ready), list_predictions
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx
from typing import Any, Literal
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from dotenv import load_dotenv
from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict

from . import api_client
from .credentials import resolve_bearer_token
from .defaults import DEFAULT_EDA_API_BASE

# Avoid loading repo-root `.env` during pytest — it often enables OAuth/Docker template
# vars and breaks unit tests that call tools without HTTP request context. Set
# EDA_FORCE_DOTENV=1 to load `.env` from tests when needed.
if os.environ.get("EDA_FORCE_DOTENV", "").strip().lower() in {"1", "true", "yes"} or (
    "pytest" not in sys.modules
):
    load_dotenv()

_raw_base = os.environ.get("EDA_API_BASE", "").strip()
_BASE_URL: str = api_client.normalize_api_base(
    _raw_base if _raw_base else DEFAULT_EDA_API_BASE
)
_parsed_api = urlparse(_BASE_URL)
if _parsed_api.scheme != "https" or not _parsed_api.netloc:
    raise RuntimeError(
        f"Invalid EDA_API_BASE after normalization: {_BASE_URL!r}. "
        "Use an HTTPS URL with a hostname (e.g. https://api.easydeploy.ai)."
    )
_API_KEY: str = os.environ.get("EDA_API_KEY", "")
_UI_BASE_URL: str = os.environ.get("EDA_UI_BASE_URL", "https://easydeploy.ai").rstrip("/")

# Sent to clients in the MCP initialize result. Hosts add it to the agent's
# context, so it carries the division of labour the tool descriptions assume.
SERVER_INSTRUCTIONS = """\
You are the data scientist; EasyDeploy is the training, deployment and prediction platform. It runs model search, feature engineering and hyperparameter tuning, then trains, deploys and predicts. It does not clean data, derive targets, split data or evaluate on a holdout: that is your job.

Before uploading:
- Establish the decision the model supports and the target it predicts. Explore the data yourself: shape, types, missingness, target balance.
- In the train file, drop identifiers and every column not knowable at prediction time: training uses every non-target column as a feature. Write a one-sentence target definition.
- Confirm data changes and dropped columns with the user before applying them.

Split it yourself:
- Stratified 80/20 for classification, chronological for time series. No entity in both files, no duplicate rows.
- Any balancing such as SMOTE goes on the train file only; the test file stays real data.
- Upload the files separately with complete_upload dataset_type "train" and "test" ("validation" is optional). Create the model version on the train dataset version only; EasyDeploy does not check the type for you.

Reading results:
- get_model_report: the cross-validation score is estimated inside the training file, and trainingFit metrics are in-sample. Neither is a holdout result.
- Holdout validation: run_batch_prediction on the test dataset version, download the output via get_prediction, and compute the metrics yourself against the true labels. The output keeps every input column, the target included, in the original row order and adds prediction and probability_<class> columns. Keep ids in test and scoring files: prediction uses only the columns the model was trained on and ignores the rest, so ids ride along for joining results back.
- Choose the decision threshold on that holdout: an F1 sweep for classifiers, the error margin for regressors. Present the holdout numbers as the model's performance.
- Build dashboards, reports and scored lists only from real prediction output, never mock values.

Getting files in: follow start_upload's next_steps. Never paste file contents into a tool.
"""

mcp = FastMCP("EasyDeploy AI", instructions=SERVER_INSTRUCTIONS)


def _kw() -> dict:
    """Pass-through keyword args for api_client calls.

    The ``api_key`` field is the bearer token to forward as
    ``Authorization: Bearer <token>``. It can be either a static EasyDeploy
    API key (``eda_live_*``) or a per-request Cognito access JWT — the API
    accepts both via the same header. With ``EDA_OAUTH_ENABLED=1``, only the
    per-request token is used (no ``EDA_API_KEY`` fallback). Otherwise:
    per-request → module ``_API_KEY`` / ``EDA_API_KEY`` env.

    Includes caller_channel so all MCP-originated requests are tagged in
    audit logs."""
    return {
        "api_key": resolve_bearer_token(env_fallback=_API_KEY),
        "base_url": _BASE_URL,
        "caller_channel": "MCP_AGENT",
    }


def _extract_tokenized_url(url: str, token_param: str) -> tuple[str, str]:
    """Return (clean_url_without_token_query, token_value)."""
    parsed = urlparse(url)
    q = parse_qs(parsed.query, keep_blank_values=True)
    token_vals = q.pop(token_param, [])
    clean_query = urlencode(q, doseq=True)
    clean_url = urlunparse(parsed._replace(query=clean_query))
    token = token_vals[0].strip() if token_vals else ""
    return clean_url, token


# Choice parameters, typed so the tool schemas carry enums. Values mirror what the
# public API accepts (amplify/functions/public-api/routes/datasets.ts:
# VALID_VERSION_TYPES / VALID_QA_STATUSES). "" means "not set" for the
# create_dataset_version fields that only apply to one of its two modes.
DatasetType = Literal["train", "test", "validation"]
DatasetVersionType = Literal["", "raw", "qa_cleaned", "training"]
DatasetQaStatus = Literal["", "pending", "in_progress", "ready", "blocked"]


class UploadFileRef(BaseModel):
    """A file handed over by the MCP host (OpenAI Apps SDK file-parameter shape).

    Hosts that support ``openai/fileParams`` replace this argument with a
    short-lived ``download_url`` plus a ``file_id``. The model never sees or
    writes the file's bytes; EasyDeploy fetches them server-side.
    """

    model_config = ConfigDict(extra="allow")

    download_url: str
    file_id: str
    mime_type: str | None = None
    file_name: str | None = None


def _sanitize_upload_channels(raw: Any) -> dict[str, Any] | None:
    """Return the API's ``channels`` block with upload tokens removed.

    The gateway channel URL carries a single-use ``uploadToken`` query param; the
    token belongs only in the ``X-Upload-Token`` header of ``curl_command``. The
    ``fromUrl`` and ``status`` channel URLs carry no secrets and pass through.
    """
    if not isinstance(raw, dict):
        return None
    channels: dict[str, Any] = {}
    for name, channel in raw.items():
        if not isinstance(channel, dict):
            continue
        entry = dict(channel)
        url = str(entry.get("url", "")).strip()
        if url:
            entry["url"], _token = _extract_tokenized_url(url, "uploadToken")
        channels[name] = entry
    return channels or None


def _ui_url(path: str) -> str:
    p = path if path.startswith("/") else f"/{path}"
    return f"{_UI_BASE_URL}{p}"


def _project_ui_url(project_id: str) -> str:
    return _ui_url(f"/projects/{project_id}")


def _model_ui_url(project_id: str, model_id: str) -> str:
    return _ui_url(f"/projects/{project_id}/models/{model_id}")


def _predictions_ui_url(project_id: str) -> str:
    return _ui_url(f"/projects/{project_id}/predictions")


def _dataset_ui_url(project_id: str, dataset_id: str) -> str:
    return _ui_url(f"/projects/{project_id}/datasets/{dataset_id}")



_PREDICTION_SAFE_KEYS = frozenset({
    "id", "status", "type", "projectId", "modelVersionId",
    "createdAt", "startTime", "endTime", "inferenceLatency",
    "output", "error",
})


def _sanitize_prediction(raw: dict[str, Any]) -> dict[str, Any]:
    """Return only agent-safe fields from a prediction response.

    Strips S3 paths (outputDataPath, inputDataPath), raw stored input
    (adhocInput/adhocOutput), and internal auth fields.
    """
    result = {k: v for k, v in raw.items() if k in _PREDICTION_SAFE_KEYS}
    if raw.get("status") == "COMPLETED" and raw.get("outputDataPath"):
        result["batch_output_available"] = True
    return result


_MODEL_VERSION_SAFE_KEYS = frozenset({
    "id", "modelId", "version", "status", "targetFeature",
    "timeSeriesMode", "timeColumn",
    "datasetVersionId", "trainingJobId", "trainingTime",
    "edaReportStatus", "edaReportReadyAt", "edaReportError",
    "edaReportSummary", "edaReportPerformanceSummary",
    "createdAt", "updatedAt",
})


def _sanitize_model_version(raw: dict[str, Any]) -> dict[str, Any]:
    """Return only agent-safe fields from a model version response.

    Strips S3 paths (fileUrl, s3OutputPath) and internal auth fields.
    """
    return {k: v for k, v in raw.items() if k in _MODEL_VERSION_SAFE_KEYS}


def _normalize_column_names(value: Any) -> Any:
    """Replace every ``columnNamesJson`` string with a parsed ``columnNames`` list.

    The API stores column names as a JSON-encoded string. Agents read a real
    list more reliably, so each dict (recursively) that carries
    ``columnNamesJson`` gets ``columnNames`` instead. If the string does not
    parse to a list, the raw value stays under ``columnNamesJson`` untouched.
    Mutates in place and returns the same object.
    """
    if isinstance(value, list):
        for item in value:
            _normalize_column_names(item)
        return value
    if not isinstance(value, dict):
        return value
    for child in value.values():
        if isinstance(child, (dict, list)):
            _normalize_column_names(child)
    if "columnNamesJson" not in value:
        return value
    raw = value["columnNamesJson"]
    parsed: Any = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return value
    if parsed is None or isinstance(parsed, list):
        value.pop("columnNamesJson")
        value["columnNames"] = parsed
    return value


# ── Tool annotations ───────────────────────────────────────────────────────────
# MCP hints (see mcp.types.ToolAnnotations). No tool deletes anything, so every
# write is destructiveHint=False. upload_from_url and start_upload (with a host
# ``file``) make EasyDeploy fetch an external URL, so they are openWorldHint=True.

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False,
)


def _write(*, idempotent: bool, open_world: bool = False) -> ToolAnnotations:
    return ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=idempotent,
        openWorldHint=open_world,
    )


# ── Account ────────────────────────────────────────────────────────────────────


@mcp.tool(annotations=_READ_ONLY)
async def get_account_status(customer_id: str = "") -> dict[str, Any]:
    """
    Get current account status: tier, training credits, prediction usage, endpoint limits.
    customer_id is optional; the backend resolves the account from the API key.
    """
    return await api_client.get_account_status(customer_id, **_kw())


# ── Projects ───────────────────────────────────────────────────────────────────


@mcp.tool(annotations=_READ_ONLY)
async def list_projects() -> list[dict[str, Any]]:
    """
    List all projects for this API key (id, name, description, timestamps).
    Call this first to obtain project IDs needed by other tools.
    """
    projects = await api_client.list_projects(**_kw())
    for item in projects:
        pid = str(item.get("id", "")).strip()
        if pid:
            item["ui_url"] = _project_ui_url(pid)
    return projects


@mcp.tool(annotations=_READ_ONLY)
async def get_project(project_id: str) -> dict[str, Any]:
    """Fetch a single project by id."""
    data = await api_client.get_project(project_id, **_kw())
    pid = str(data.get("id", "")).strip() or project_id.strip()
    if pid:
        data["ui_url"] = _project_ui_url(pid)
    return data


@mcp.tool(annotations=_write(idempotent=False))
async def create_project(
    name: str,
    description: str = "",
    project_id: str = "",
) -> dict[str, Any]:
    """
    Create or update a project (create-or-update: not idempotent).

    - **Create**: call with ``name`` (and optional ``description``). Each call
      without ``project_id`` creates another project.
    - **Update/rename**: pass ``project_id`` of an existing project plus the
      fields to change (``name`` and/or ``description``); this PATCHes it.
    """
    pid = project_id.strip()
    if pid:
        body: dict[str, str] = {}
        if name.strip():
            body["name"] = name.strip()
        if description.strip():
            body["description"] = description.strip()
        data = await api_client.update_project(pid, body, **_kw())
    else:
        desc = description.strip() if description else ""
        body = {"name": name.strip(), "description": desc or name.strip()}
        data = await api_client.create_project(body, **_kw())
    pid = str(data.get("id", "")).strip() or pid
    if pid:
        data["ui_url"] = _project_ui_url(pid)
    return data


# ── Datasets ───────────────────────────────────────────────────────────────────


@mcp.tool(annotations=_READ_ONLY)
async def list_datasets(project_id: str) -> list[dict[str, Any]]:
    """List datasets in a project (id, name, type, timestamps)."""
    datasets = await api_client.list_datasets(project_id, **_kw())
    for item in datasets:
        did = str(item.get("id", "")).strip()
        if did:
            item["ui_url"] = _dataset_ui_url(project_id, did)
    return datasets


@mcp.tool(annotations=_READ_ONLY)
async def get_dataset(project_id: str, dataset_id: str) -> dict[str, Any]:
    """
    Fetch one dataset by id (name, description, type, timestamps). Read-only.

    Datasets are *created* via ``complete_upload`` (the upload flow). To rename
    a dataset or change its description, use ``update_dataset``.
    """
    data = await api_client.get_dataset(project_id, dataset_id, **_kw())
    _normalize_column_names(data)
    did = str(data.get("id", "")).strip() or dataset_id.strip()
    if did:
        data["ui_url"] = _dataset_ui_url(project_id, did)
    return data


@mcp.tool(annotations=_write(idempotent=True))
async def update_dataset(
    project_id: str,
    dataset_id: str,
    name: str = "",
    description: str = "",
) -> dict[str, Any]:
    """
    Rename a dataset or change its description.

    Pass at least one of ``name`` or ``description``; fields left empty are not
    changed. Repeating the same call leaves the dataset in the same state.
    Returns the updated dataset record.
    """
    body: dict[str, str] = {}
    if name.strip():
        body["name"] = name.strip()
    if description.strip():
        body["description"] = description.strip()
    if not body:
        raise ValueError("Pass name and/or description to update the dataset")
    data = await api_client.update_dataset(project_id, dataset_id, body, **_kw())
    _normalize_column_names(data)
    did = str(data.get("id", "")).strip() or dataset_id.strip()
    if did:
        data["ui_url"] = _dataset_ui_url(project_id, did)
    return data


_MODEL_BUILDER_URL = "https://www.easydeploy.ai/model-builder"


def _complete_upload_args(project_id: str, upload_request_id: str, dataset_id: str) -> str:
    """The complete_upload argument hint used in next_steps runbooks."""
    args = f"project_id='{project_id}', upload_request_id='{upload_request_id}'"
    if dataset_id:
        return (
            f"{args}, dataset_id='{dataset_id}' and any name (required but ignored: "
            "this adds a new version and the dataset keeps its existing name)"
        )
    return f"{args}, and a dataset name (a new dataset is created)"


def _gateway_runbook(project_id: str, upload_request_id: str, dataset_id: str) -> str:
    """The ordered fallback runbook returned by start_upload on the curl path."""
    return (
        "1. Run curl_command in bash with FILE_PATH replaced by the real file path. "
        "The gateway accepts at most 6 MB and answers 413 above that — do not split the "
        "file and do not paste its contents into any tool.\n"
        "2. If curl fails with a network or connection error (could not resolve host, "
        "connection refused, timeout), this sandbox has no egress. Do NOT paste file "
        "contents into any tool. Instead: if the file is already at a shareable URL (a "
        "Google Sheets or Drive link set to anyone-with-link, or a file link this host "
        f"provides), call upload_from_url with upload_request_id='{upload_request_id}' and "
        "that URL. Otherwise save the training file, give it to the user as a download, and "
        f"tell them to upload it at {_MODEL_BUILDER_URL}. Mention that enabling network "
        "access for this host, where their plan allows it, avoids the detour next time.\n"
        "3. After a model-builder upload, ask the user for the dataset name they entered or "
        "the dataset URL shown on the page. Resolve it with list_datasets (match the name) "
        "or by parsing the project and dataset ids out of the URL, confirm the row count "
        "matches the file you handed over, and continue from that dataset id. Do not guess "
        "which dataset is theirs. A model-builder upload is already registered as a dataset "
        "— do not call complete_upload for it.\n"
        f"4. Otherwise poll get_upload_status with upload_request_id='{upload_request_id}' "
        "until status is READY (REJECTED means the validator refused the file — read error "
        "and fix it), then call complete_upload with "
        f"{_complete_upload_args(project_id, upload_request_id, dataset_id)}."
    )


@mcp.tool(
    # With a host ``file``, EasyDeploy fetches that external download URL.
    annotations=_write(idempotent=False, open_world=True),
    meta={"openai/fileParams": ["file"]},
)
async def start_upload(
    filename: str,
    project_id: str,
    dataset_id: str = "",
    file: UploadFileRef | None = None,
) -> dict[str, Any]:
    """
    Open an upload session and return the best byte channel for this host.

    One session carries one file. Your train and test files (and an optional
    validation file) are separate uploads, each with its own ``start_upload``.

    Pick a channel — file bytes must never travel through tool arguments or the
    conversation on any of them:

    **A. Host file parameter (preferred where available).** Hosts that support
    ``openai/fileParams`` (Codex, ChatGPT) hydrate ``file`` from a sandbox path
    you name. EasyDeploy then fetches the bytes server-side and the session goes
    straight to ``RECEIVING`` — no network needed from the sandbox. Poll
    ``get_upload_status`` until ``READY``, then call ``complete_upload``.

    **B. Gateway PUT (default).** Called without ``file``, this returns
    ``curl_command``: replace FILE_PATH and run it in bash. Max ~6 MB; no API key
    or auth header goes in the curl command.

    **C. Fetch from a share link.** If the sandbox cannot reach the network but
    the file sits at a shareable URL, call ``upload_from_url`` with the returned
    ``upload_request_id``.

    The returned ``next_steps`` is the ordered runbook, including the
    model-builder fallback for hosts with no byte channel at all.

    **dataset_id:** omit it to create a new dataset when ``complete_upload``
    runs. Pass an existing dataset's id to add a new version to that dataset
    instead; ``complete_upload``'s ``name`` is then ignored and the dataset
    keeps its current name. The output carries ``dataset_id`` only when you
    passed one.
    """
    ds = dataset_id.strip() or None
    caller_dataset_id = ds or ""
    data = await api_client.get_upload_url(
        filename, project_id, **_kw(), dataset_id=ds,
    )

    upload_request_id = str(data.get("uploadRequestId", "")).strip()
    channels = _sanitize_upload_channels(data.get("channels"))
    fallback = data.get("fallback")

    if file is not None:
        source_url = str(file.download_url or "").strip()
        if not source_url:
            raise ValueError("file.download_url is empty; the host did not hydrate the file")
        if not upload_request_id:
            raise RuntimeError("uploadRequestId missing from API response")
        # The URL is handed to the API and never echoed back to the model.
        await api_client.upload_from_url(upload_request_id, source_url, **_kw())
        out: dict[str, Any] = {
            "upload_request_id": upload_request_id,
            "status": "RECEIVING",
            "channel": "file",
            "next_steps": (
                "Call get_upload_status with upload_request_id until status is READY "
                "(or REJECTED, then read error). Then call complete_upload with "
                f"{_complete_upload_args(project_id, upload_request_id, caller_dataset_id)}."
            ),
        }
        if caller_dataset_id:
            out["dataset_id"] = caller_dataset_id
        if channels is not None:
            out["channels"] = channels
        if fallback is not None:
            out["fallback"] = fallback
        return out

    gateway_url_raw = str(data.get("gatewayUploadUrl", "")).strip()
    if not gateway_url_raw:
        raise RuntimeError("Gateway upload URL missing from API response")
    gateway_url, upload_token = _extract_tokenized_url(gateway_url_raw, "uploadToken")
    if not upload_token:
        raise RuntimeError("Gateway upload URL is missing uploadToken")

    data["curl_command"] = (
        f'curl -X PUT '
        f'-H "Content-Type: text/csv" '
        f'-H "X-Upload-Token: {upload_token}" '
        f'-T "FILE_PATH" '
        f'"{gateway_url}"'
    )
    data["next_steps"] = _gateway_runbook(
        project_id, upload_request_id, caller_dataset_id
    )
    # Never let the raw channels block through: its gateway url carries the token.
    data.pop("channels", None)
    if channels is not None:
        data["channels"] = channels

    data["upload_request_id"] = upload_request_id
    data.pop("uploadRequestId", None)
    # The API pre-assigns a dataset id for new uploads; surfacing it invites
    # agents to pass it back as if the dataset already existed.
    data.pop("datasetId", None)
    if caller_dataset_id:
        data["dataset_id"] = caller_dataset_id
    data.pop("bucket", None)
    data.pop("fileUrl", None)
    data.pop("s3Key", None)
    data.pop("gatewayUploadUrl", None)
    data.pop("uploadUrl", None)

    return data


@mcp.tool(annotations=_write(idempotent=False, open_world=True))
async def upload_from_url(upload_request_id: str, source_url: str) -> dict[str, Any]:
    """
    Hand EasyDeploy a URL to fetch an upload session's bytes from, server-side.

    Use this when the sandbox has no network egress (the gateway curl failed with
    a connection error) but the training file already lives at a shareable URL, or
    when a host handed you a file download link.

    Accepted sources:
      - Google Sheets and Google Drive share links set to **anyone with the link**
        (Sheets are exported as CSV).
      - OpenAI file links from a host file parameter.
      - Any other host on the EasyDeploy source allowlist.

    EasyDeploy fetches the bytes itself, up to 256 MB. The file never passes through the model,
    the conversation, or this tool's arguments — pass a URL, never file contents.
    Private, loopback, and metadata addresses are rejected, as is any host off the
    allowlist; the error explains which rule refused the URL.

    Returns the session in ``RECEIVING``. A 409 means the session already received
    bytes or expired — call ``start_upload`` again for a fresh one.
    """
    rid = upload_request_id.strip()
    if not rid:
        raise ValueError("upload_request_id is required (from start_upload)")
    url = source_url.strip()
    if not url:
        raise ValueError("source_url is required")
    out = await api_client.upload_from_url(rid, url, **_kw())
    if isinstance(out, dict):
        out["next_steps"] = (
            f"Poll get_upload_status with upload_request_id='{rid}' every 3-5 s until "
            "status is READY, then call complete_upload with project_id, "
            "upload_request_id and a dataset name (plus dataset_id only if you passed "
            "one to start_upload). REJECTED means the "
            "validator refused the file — read error, fix the file, and start a new "
            "upload."
        )
    return out


_UPLOAD_STATUS_NEXT_STEPS: dict[str, str] = {
    "URL_ISSUED": (
        "No bytes received yet. Run the curl_command from start_upload, or call "
        "upload_from_url with a shareable source URL."
    ),
    "RECEIVING": "Bytes are still arriving. Wait 3-5 s and call get_upload_status again.",
    "UPLOADED": (
        "Bytes landed and validation is queued. Wait 3-5 s and call get_upload_status again."
    ),
    "VALIDATING": (
        "The file is being validated. Wait 3-5 s and call get_upload_status again."
    ),
    "READY": (
        "The file passed validation. Call complete_upload with project_id, "
        "upload_request_id and a dataset name (plus dataset_id only if you passed one "
        "to start_upload) to register the dataset version."
    ),
    "REJECTED": (
        "Validation refused the file. Read the error field, fix the file (headers, "
        "delimiter, encoding, empty rows), then call start_upload for a new session. "
        "Do not retry this upload_request_id."
    ),
    "EXPIRED": (
        "The upload session expired. Call start_upload again to get a fresh session."
    ),
    "CONSUMED": (
        "This upload is already registered as a dataset version. Use "
        "list_dataset_versions (or get_dataset) to work with it; do not call "
        "complete_upload again."
    ),
}


@mcp.tool(annotations=_READ_ONLY)
async def get_upload_status(upload_request_id: str) -> dict[str, Any]:
    """
    Poll an upload session by the ``upload_request_id`` from ``start_upload``.

    ``status`` is one of URL_ISSUED, RECEIVING, UPLOADED, VALIDATING, READY,
    REJECTED, CONSUMED, EXPIRED. Every channel (gateway PUT, host file parameter,
    fetch-from-URL) reports through this one tool.

    ``complete_upload`` only accepts **READY** — poll here first. On REJECTED, the
    ``error`` field names the validation check that failed; fix the file and start
    a new upload rather than retrying this session.

    Also returns ``sizeBytes`` and ``rowCount`` once known — confirm the row count
    matches the file you handed over before registering the dataset.
    """
    rid = upload_request_id.strip()
    if not rid:
        raise ValueError("upload_request_id is required (from start_upload)")
    out = await api_client.get_upload_status(rid, **_kw())
    if isinstance(out, dict):
        status = str(out.get("status", "")).strip().upper()
        guidance = _UPLOAD_STATUS_NEXT_STEPS.get(status)
        if guidance is None:
            guidance = (
                f"Unrecognized status {status or 'MISSING'!r}. Read nextStep from the "
                "response; poll again in 3-5 s if it is not terminal."
            )
        out["next_steps"] = guidance
    return out


@mcp.tool(annotations=_write(idempotent=False))
async def complete_upload(
    project_id: str,
    name: str,
    upload_request_id: str,
    description: str = "",
    dataset_type: DatasetType = "train",
    dataset_id: str = "",
) -> dict[str, Any]:
    """
    Register a validated upload as a dataset (the last step of every channel).

    upload_request_id: opaque id returned by start_upload.
    dataset_id: omit it to create a new dataset named ``name``. Pass an
      existing dataset's id (the one given to start_upload) to add a new
      version to that dataset; ``name`` is then still required but ignored,
      and the dataset keeps its current name.
    dataset_type: train | test | validation (default train). You produce these
      files by splitting the prepared data yourself: ``train`` is what the model
      learns from, ``test`` is the holdout you score afterwards with
      ``run_batch_prediction``, and ``validation`` is an optional extra holdout.
      EasyDeploy stores the type as a label and never splits for you.

    **The upload session must be in status READY** — not ``UPLOADED``. READY means
    the validator has accepted and promoted the bytes. Any other state returns
    **400** naming the current state and what to do: ``RECEIVING`` / ``UPLOADED`` /
    ``VALIDATING`` are still in flight, ``REJECTED`` failed validation, ``EXPIRED``
    needs a new ``start_upload``, ``CONSUMED`` is already a dataset version. Call
    ``get_upload_status`` first and wait for READY instead of calling this tool on
    a guess.

    A file the user uploaded through https://www.easydeploy.ai/model-builder is
    already a dataset — resolve it with ``list_datasets`` rather than calling this.

    Returns ``dataset`` (id, name, ui_url) and ``datasetVersion`` once, at the
    top level, with its ``ui_url`` and ``columnNames`` as a list.

    ``qa_status`` is informational and does not gate anything. ``version_type``
    matters in one case: ``submit_training_job`` with an explicit
    ``dataset_version_id`` rejects a ``raw`` version. Omit ``dataset_version_id``
    to train on the model version's own dataset.
    """
    body: dict = {
        "uploadRequestId": upload_request_id.strip(),
        "name": name.strip(),
        "datasetType": (dataset_type or "train").strip() or "train",
    }
    if description.strip():
        body["description"] = description.strip()
    if dataset_id.strip():
        body["datasetId"] = dataset_id.strip()
    out = await api_client.complete_dataset_upload(project_id, body, **_kw())
    dataset = out.get("dataset") if isinstance(out, dict) else None
    if isinstance(dataset, dict):
        # The API nests the same version under dataset.datasetVersion too.
        dataset.pop("datasetVersion", None)
    _normalize_column_names(out)
    dataset_version = out.get("datasetVersion") if isinstance(out, dict) else None
    dataset_id = ""
    if isinstance(dataset, dict):
        dataset_id = str(dataset.get("id", "")).strip()
        if dataset_id:
            dataset["ui_url"] = _dataset_ui_url(project_id, dataset_id)
    if dataset_id and isinstance(dataset_version, dict):
        version = dataset_version.get("version")
        if version is not None:
            dataset_version["ui_url"] = f"{_dataset_ui_url(project_id, dataset_id)}?version={version}"
        else:
            dataset_version["ui_url"] = _dataset_ui_url(project_id, dataset_id)
    return out


# ── Dataset versions ───────────────────────────────────────────────────────────


@mcp.tool(annotations=_READ_ONLY)
async def list_dataset_versions(dataset_id: str, project_id: str = "") -> list[dict[str, Any]]:
    """
    List all versions of a dataset (version number, version_type, qa_status, row counts).

    ``project_id`` is optional — the backend resolves access from ``dataset_id`` when omitted.
    Column names, where present, come back as a ``columnNames`` list.

    ``qa_status`` is informational and does not gate anything. ``version_type``
    matters in one case: ``submit_training_job`` with an explicit
    ``dataset_version_id`` rejects a ``raw`` version. Omit ``dataset_version_id``
    to train on the model version's own dataset.
    """
    versions = await api_client.list_dataset_versions(dataset_id, project_id.strip(), **_kw())
    _normalize_column_names(versions)
    pid = project_id.strip() or (
        str(versions[0].get("projectId", "")).strip() if versions else ""
    )
    if not pid:
        return versions
    base = _dataset_ui_url(pid, dataset_id)
    for item in versions:
        version = item.get("version")
        item["ui_url"] = f"{base}?version={version}" if version is not None else base
    return versions


@mcp.tool(annotations=_READ_ONLY)
async def get_dataset_version(project_id: str, dataset_id: str, version_id: str) -> dict[str, Any]:
    """
    Fetch one dataset version by id (row and column counts, ``columnNames`` list,
    qa_status, version_type).

    ``qa_status`` is informational and does not gate anything. ``version_type``
    matters in one case: ``submit_training_job`` with an explicit
    ``dataset_version_id`` rejects a ``raw`` version. Omit ``dataset_version_id``
    to train on the model version's own dataset.
    """
    item = await api_client.get_dataset_version(project_id, dataset_id, version_id, **_kw())
    _normalize_column_names(item)
    base = _dataset_ui_url(project_id, dataset_id)
    version = item.get("version") if isinstance(item, dict) else None
    if isinstance(item, dict):
        item["ui_url"] = f"{base}?version={version}" if version is not None else base
    return item


@mcp.tool(annotations=_write(idempotent=False))
async def create_dataset_version(
    project_id: str,
    dataset_id: str,
    version_type: DatasetVersionType = "",
    file_url: str = "",
    qa_metadata: dict[str, Any] | None = None,
    version_id: str = "",
    qa_status: DatasetQaStatus = "",
) -> dict[str, Any]:
    """
    Create or update a dataset version.

    **Create** (register an S3 file as a new version):
      Required: ``version_type`` (raw | qa_cleaned | training), ``file_url`` (s3:// URL),
      ``qa_metadata`` (freeform JSON with QA results).

    **Update** (change qa_status on an existing version):
      Required: ``version_id``, ``qa_status`` (pending | in_progress | ready | blocked).

    ``qa_status`` is informational and does not gate anything. ``version_type``
    matters in one case: ``submit_training_job`` with an explicit
    ``dataset_version_id`` rejects a ``raw`` version. Omit ``dataset_version_id``
    to train on the model version's own dataset.
    """
    vid = version_id.strip()
    if vid:
        qs = qa_status.strip()
        if not qs:
            raise ValueError("qa_status is required when updating a dataset version")
        out = await api_client.patch_dataset_version(
            project_id, dataset_id, vid, {"qa_status": qs}, **_kw()
        )
        _normalize_column_names(out)
        if isinstance(out, dict):
            base = _dataset_ui_url(project_id, dataset_id)
            version = out.get("version")
            out["ui_url"] = f"{base}?version={version}" if version is not None else base
        return out

    if not version_type.strip() or not file_url.strip():
        raise ValueError("version_type and file_url are required to create a dataset version")
    s3_key = file_url.split("s3://", 1)[-1]
    if "/" in s3_key:
        s3_key = s3_key.split("/", 1)[1]
    body: dict[str, Any] = {
        "s3Key": s3_key,
        "version_type": version_type,
        "qa_metadata": qa_metadata or {},
    }
    out = await api_client.create_dataset_version(project_id, dataset_id, body, **_kw())
    _normalize_column_names(out)
    base = _dataset_ui_url(project_id, dataset_id)
    dataset_version = out.get("datasetVersion") if isinstance(out, dict) else None
    if isinstance(dataset_version, dict):
        version = dataset_version.get("version")
        dataset_version["ui_url"] = f"{base}?version={version}" if version is not None else base
    return out


# ── Models ─────────────────────────────────────────────────────────────────────


@mcp.tool(annotations=_write(idempotent=False))
async def create_model(
    project_id: str,
    name: str,
    description: str = "",
    model_id: str = "",
) -> dict[str, Any]:
    """
    Create or update a model (create-or-update: not idempotent).

    - **Create**: call with ``project_id`` and ``name`` (and optional ``description``).
      Each call without ``model_id`` creates another model.
    - **Update/rename**: also pass ``model_id`` plus the fields to change; this
      PATCHes the existing model.
    """
    mid = model_id.strip()
    if mid:
        body: dict[str, str] = {}
        if name.strip():
            body["name"] = name.strip()
        if description.strip():
            body["description"] = description.strip()
        data = await api_client.update_model(project_id, mid, body, **_kw())
    else:
        desc = description.strip() if description else ""
        body = {
            "name": name.strip(),
            "description": desc or f"Predicts {name.strip()}",
        }
        data = await api_client.create_model(project_id, body, **_kw())
    mid = str(data.get("id", "")).strip() or mid
    if mid:
        data["ui_url"] = _model_ui_url(project_id, mid)
    return data


@mcp.tool(annotations=_READ_ONLY)
async def get_model(project_id: str, model_id: str) -> dict[str, Any]:
    """Fetch a single model by id (name, description, version count)."""
    data = await api_client.get_model(project_id, model_id, **_kw())
    mid = str(data.get("id", "")).strip() or model_id.strip()
    if mid:
        data["ui_url"] = _model_ui_url(project_id, mid)
    return data


@mcp.tool(annotations=_write(idempotent=False))
async def create_model_version(
    project_id: str,
    model_id: str,
    dataset_version_id: str,
    target_feature: str,
    time_series_mode: bool = False,
    time_column: str = "",
) -> dict[str, Any]:
    """
    Create a model version tied to a dataset version and target column.
    Then call submit_training_job with the returned model version id.

    Pass the **train** dataset's version, never the test one: a test file that
    reaches training is no longer a holdout. EasyDeploy does not check the type.

    Set time_series_mode=true and time_column when forecasting ordered periods
    (e.g. weekly business metrics). Time-series mode uses forward-chaining
    cross-validation (TimeSeriesSplit) instead of shuffled k-fold: each fold
    trains on earlier periods and scores on later ones. ``get_model_report``
    records the strategy a run used in ``metrics.crossValidation.strategy``
    (``time_series_split`` for forward-chaining).
    """
    body: dict[str, Any] = {
        "datasetVersionId": dataset_version_id,
        "targetFeature": target_feature.strip(),
    }
    if time_series_mode:
        if not time_column.strip():
            raise ValueError("time_column is required when time_series_mode is true")
        if time_column.strip() == target_feature.strip():
            raise ValueError("time_column must differ from target_feature")
        body["timeSeriesMode"] = True
        body["timeColumn"] = time_column.strip()
    out = await api_client.create_model_version(project_id, model_id, body, **_kw())
    if isinstance(out, dict):
        out["ui_url"] = _model_ui_url(project_id, model_id)
    return out


@mcp.tool(annotations=_READ_ONLY)
async def list_models(project_id: str) -> list[dict[str, Any]]:
    """List all models in a project (id, name)."""
    models = await api_client.list_models(project_id, **_kw())
    for item in models:
        mid = str(item.get("id", "")).strip()
        if mid:
            item["ui_url"] = _model_ui_url(project_id, mid)
    return models


@mcp.tool(annotations=_READ_ONLY)
async def list_model_versions(project_id: str, model_id: str) -> list[dict[str, Any]]:
    """
    List model versions.
    Training state is `status` (SUBMITTED → TRAINING → TRAINING_COMPLETED or TRAINING_FAILED).
    Report readiness is `edaReportStatus` (PENDING | GENERATING | READY | FAILED).
    """
    raw = await api_client.list_model_versions(project_id, model_id, **_kw())
    out = [_sanitize_model_version(v) for v in raw]
    base = _model_ui_url(project_id, model_id)
    for item, source in zip(out, raw):
        version = source.get("version")
        item["ui_url"] = f"{base}?version={version}" if version is not None else base
    return out


@mcp.tool(annotations=_READ_ONLY)
async def get_model_version(
    project_id: str,
    model_id: str,
    version_id: str,
) -> dict[str, Any]:
    """
    Fetch a single model version by id (status, edaReportStatus, target, timestamps).
    Prefer this over list_model_versions when you already know the version_id.
    """
    raw = await api_client.get_model_version(project_id, model_id, version_id, **_kw())
    out = _sanitize_model_version(raw)
    version = raw.get("version")
    base = _model_ui_url(project_id, model_id)
    out["ui_url"] = f"{base}?version={version}" if version is not None else base
    return out


@mcp.tool(annotations=_READ_ONLY)
async def get_model_report(
    model_id: str,
    project_id: str = "",
    model_version_id: str = "",
    full_report: bool = False,
) -> dict[str, Any]:
    """
    Load the EDA training report (metrics, feature analysis, performance summary).

    ``project_id`` is optional — the backend resolves it from ``model_id`` when omitted.

    Omit ``model_version_id`` to use the **latest** version for this model. Pass an id from
    ``list_model_versions`` to read a specific version.

    Default response is **summary** only (token-efficient). Set full_report=true for full detail.

    **How the model was validated.** Model search scores candidates by
    cross-validation: 10-fold stratified shuffled CV on ROC-AUC for classifiers,
    10-fold shuffled CV on negative MSE for regressors; a version created with
    time_series_mode uses forward-chaining CV (TimeSeriesSplit) instead. The
    winning pipeline is then refit on all rows.
    There is no separate test set. Accuracy, the confusion matrix, per-class
    precision/recall and in-sample ROC-AUC are training fit (computed on the
    same rows the model learned from), not a holdout estimate. The CV score is
    the report's out-of-sample estimate, but it is measured inside the training
    file; the holdout estimate comes from scoring your own test file with
    ``run_batch_prediction``, not from this report. The prose summary is
    written by an LLM and may use looser wording than these numbers.

    **metrics** (returned next to the report, passed through unchanged):
    ``{taskType, crossValidation: {metric, score, strategy, folds,
    strategySource ('recorded'|'platform_default'), note}, trainingFit: {note,
    accuracy?, rocAuc?, confusionMatrix?, perClass?, mse?, rmse?, mae?, r2?,
    explainedVariance?, mape?}, additionalCv?: {r2?, rmse?}, warnings: []}``.
    ``metrics`` is null when it cannot be derived, with ``metricsUnavailableReason``
    saying why. ``crossValidation.strategy`` names the CV used for that run
    (``time_series_split`` for forward-chaining); read ``warnings`` before
    quoting any number.
    """
    # Training completion != report readiness.
    # Poll ModelVersion.edaReportStatus (set by generate_eda_report Lambda) before reading S3.
    max_wait_seconds = int(os.environ.get("EDA_REPORT_MAX_WAIT_SECONDS", "300"))
    poll_interval_seconds = float(os.environ.get("EDA_REPORT_POLL_INTERVAL_SECONDS", "10"))

    pid = project_id.strip()
    if not pid:
        m = await api_client.get_model_by_id(model_id, **_kw())
        pid = str(m.get("projectId", "")).strip()
        if not pid:
            raise RuntimeError("Could not resolve project_id from model; pass project_id explicitly")

    target_version_id = model_version_id.strip()
    started_at = asyncio.get_event_loop().time()

    while True:
        versions = await api_client.list_model_versions(pid, model_id, **_kw())
        if not versions:
            raise RuntimeError("Model has no versions yet; cannot fetch report")

        chosen: dict[str, Any] | None = None
        if target_version_id:
            chosen = next((v for v in versions if str(v.get("id", "")).strip() == target_version_id), None)
        else:
            chosen = versions[0]
            target_version_id = str(chosen.get("id", "")).strip()

        if not chosen or not target_version_id:
            raise RuntimeError("Could not resolve target model version for report fetch")

        # The REST API exposes edaReportStatus; keep fallback keys for resilience.
        report_status = (
            chosen.get("edaReportStatus")
            or chosen.get("eda_report_status")
        )
        report_status_str = str(report_status).upper() if report_status is not None else ""

        if report_status_str == "READY":
            break
        if report_status_str == "FAILED":
            raise RuntimeError(f"EDA report generation failed for modelVersionId={target_version_id}")

        # Backward compatibility: if the field doesn't exist yet, use training status
        # as a best-effort proxy (schema rollout order).
        if not report_status_str:
            api_training_status = str(chosen.get("status", "")).upper()
            if api_training_status == "TRAINING_COMPLETED":
                break

        elapsed = asyncio.get_event_loop().time() - started_at
        if elapsed >= max_wait_seconds:
            raise TimeoutError(
                f"Timed out waiting for EDA report readiness (edaReportStatus != READY) for modelVersionId={target_version_id}"
            )

        await asyncio.sleep(poll_interval_seconds)

    data = await api_client.get_model_report(
        model_id,
        project_id.strip(),
        target_version_id,
        report_scope="full" if full_report else "summary",
        **_kw(),
    )
    if isinstance(data, dict):
        data["ui_url"] = _model_ui_url(pid, model_id)
    return data


# ── Training ───────────────────────────────────────────────────────────────────


@mcp.tool(annotations=_write(idempotent=False))
async def submit_training_job(
    model_version_id: str,
    dataset_version_id: str = "",
) -> dict[str, Any]:
    """
    Submit a training job for a model version.

    **Track completion:** Poll ``list_model_versions`` for the model and watch the target
    version's ``status`` until it reaches TRAINING_COMPLETED or TRAINING_FAILED (same as the
    web UI). This is the most reliable approach across MCP hosts.

    Or pass the returned ``jobId`` to ``get_training_status`` with ``wait=true`` to block
    until the job finishes (typical 2–3 min).

    **dataset_version_id** can be omitted when the model version was created with
    ``create_model_version`` in the same flow — the backend resolves target_feature,
    file, and dataset from the model version record automatically. Omit it to train
    on the model version's own dataset. Pass it only to override with a different
    ``qa_cleaned`` or ``training`` version: an explicit ``dataset_version_id`` whose
    ``version_type`` is ``raw`` (every upload starts as ``raw``) is rejected with
    400 INVALID_VERSION_TYPE.

    Returns ``{jobId, modelVersionId, status}``.
    """
    body: dict = {"modelVersionId": model_version_id}
    if dataset_version_id:
        body["datasetVersionId"] = dataset_version_id
    return await api_client.submit_training_job(body, **_kw())


@mcp.tool(annotations=_READ_ONLY)
async def get_training_status(
    job_id: str,
    wait: bool = False,
    timeout_seconds: int = 180,
    poll_interval_seconds: float = 10.0,
) -> dict[str, Any]:
    """
    Check a training job by **job_id** (the ``jobId`` field from ``submit_training_job``).

    Response fields:
      - status: PENDING | RUNNING | COMPLETE | FAILED
      - trainingTimeSeconds: wall-clock seconds once the job stops; null while running
      - modelVersionId: the model version being trained

    By default returns the current status immediately.

    Set **wait=true** to block until the job reaches a terminal state (COMPLETE or
    FAILED). Polls every ``poll_interval_seconds`` (default 10 s) for up to
    ``timeout_seconds`` (default 180 s / 3 min). Typical training runs finish in
    2-3 minutes. If the timeout expires, the last polled status is returned with
    ``timed_out: true`` and ``next_steps``: the job is still running, so call this
    tool again with the same job_id — do not resubmit the training job.
    """
    _NON_RETRYABLE = {401, 403, 404}

    data = await api_client.get_training_status(job_id, **_kw())
    if not wait:
        return data

    terminal = {"COMPLETE", "FAILED"}
    status = str(data.get("status", "")).upper()
    if status in terminal:
        return data

    timeout = max(1, int(timeout_seconds))
    interval = max(1.0, float(poll_interval_seconds))
    started = asyncio.get_event_loop().time()
    while True:
        elapsed = asyncio.get_event_loop().time() - started
        if elapsed >= timeout:
            data["timed_out"] = True
            data["next_steps"] = (
                f"Training is still running; nothing failed. Call get_training_status "
                f"again with job_id='{job_id}' (wait=true to keep blocking). Do not call "
                "submit_training_job again — that would start a second job and spend "
                "another training credit."
            )
            return data
        await asyncio.sleep(interval)
        try:
            data = await api_client.get_training_status(job_id, **_kw())
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in _NON_RETRYABLE:
                raise
            continue
        status = str(data.get("status", "")).upper()
        if status in terminal:
            return data


# ── Predictions ────────────────────────────────────────────────────────────────


@mcp.tool(annotations=_write(idempotent=False))
async def run_prediction(
    model_version_id: str,
    input_data: dict[str, Any],
    project_id: str = "",
    target_feature: str = "",
    wait_for_result: bool = True,
    max_wait_seconds: int = 90,
    poll_interval_seconds: float = 2.0,
) -> dict[str, Any]:
    """
    Run a single ad-hoc prediction against a trained model version.

    ``project_id`` and ``target_feature`` are auto-resolved from the model version
    record when omitted. By default waits and returns the result inline (label +
    probability). Set ``wait_for_result=false`` to return immediately with prediction_id.
    """
    body: dict[str, Any] = {"modelVersionId": model_version_id, "input": input_data}
    if project_id.strip():
        body["projectId"] = project_id.strip()
    if target_feature.strip():
        body["targetFeature"] = target_feature.strip()
    data = await api_client.run_prediction(body, **_kw())
    prediction_id = str(data["id"])
    resolved_project = project_id.strip() or str(data.get("projectId", "")).strip()

    if not wait_for_result:
        result: dict[str, Any] = {
            "prediction_id": prediction_id,
            "status": str(data.get("status", "PENDING")),
        }
        if resolved_project:
            result["ui_url"] = _predictions_ui_url(resolved_project)
        return result

    started_at = asyncio.get_event_loop().time()
    while True:
        prediction = await api_client.get_prediction(prediction_id, **_kw())
        status = str(prediction.get("status", "")).upper()

        if status in ("COMPLETED", "FAILED"):
            out = _sanitize_prediction(prediction)
            pid = resolved_project or str(prediction.get("projectId", "")).strip()
            if pid:
                out["ui_url"] = _predictions_ui_url(pid)
            return out

        elapsed = asyncio.get_event_loop().time() - started_at
        if elapsed >= max_wait_seconds:
            result = {
                "prediction_id": prediction_id,
                "status": status or "PENDING",
                "timed_out": True,
            }
            if resolved_project:
                result["ui_url"] = _predictions_ui_url(resolved_project)
            return result
        await asyncio.sleep(poll_interval_seconds)


@mcp.tool(annotations=_write(idempotent=False))
async def run_batch_prediction(
    model_version_id: str,
    dataset_version_id: str,
    project_id: str = "",
    target_feature: str = "",
    wait_for_result: bool = False,
    max_wait_seconds: int = 600,
    poll_interval_seconds: float = 5.0,
) -> dict[str, Any]:
    """
    Score an entire dataset against a trained model version.

    ``project_id`` and ``target_feature`` are auto-resolved from the model version
    record when omitted. ``dataset_version_id`` identifies both the input file and
    the row count for credit billing (one prediction credit per row).

    **Holdout validation:** score your **test** dataset version with its labels
    still in it. The CSV you download via ``get_prediction`` is that file in its
    original row order with every column kept (target and ids included), plus
    ``prediction`` and, for classifiers, one ``probability_<class>`` column per
    class (``probability_1`` is the positive class for a 0/1 target); compute
    the holdout metrics and decision threshold yourself from it, since
    EasyDeploy does not return them. For a string target the probability
    columns are named by encoded class index (``probability_0``,
    ``probability_1``, ... in sorted label order), not by label. Columns the
    model was not trained on, such as ids, are ignored for scoring and kept in
    the output, so leave them in to join results back; every training feature
    must be present or the job fails naming the missing one. The download
    is capped at 9 MB, so keep test files small enough that their scored output
    fits, or split the test set across several batches.

    Returns immediately by default (fire-and-poll). Use
    ``get_prediction(prediction_id)`` to check status (includes ``downloadReady``
    flag for completed batches). Set ``wait_for_result=true`` to block.
    """
    body: dict[str, Any] = {
        "modelVersionId": model_version_id,
        "datasetVersionId": dataset_version_id,
    }
    if project_id.strip():
        body["projectId"] = project_id.strip()
    if target_feature.strip():
        body["targetFeature"] = target_feature.strip()

    data = await api_client.run_prediction(body, **_kw())
    prediction_id = str(data["id"])
    resolved_project = project_id.strip() or str(data.get("projectId", "")).strip()

    if not wait_for_result:
        result: dict[str, Any] = {
            "prediction_id": prediction_id,
            "status": str(data.get("status", "PENDING")),
        }
        if resolved_project:
            result["ui_url"] = _predictions_ui_url(resolved_project)
        return result

    started_at = asyncio.get_event_loop().time()
    while True:
        prediction = await api_client.get_prediction(prediction_id, **_kw())
        status = str(prediction.get("status", "")).upper()

        if status in ("COMPLETED", "FAILED"):
            out = _sanitize_prediction(prediction)
            pid = resolved_project or str(prediction.get("projectId", "")).strip()
            if pid:
                out["ui_url"] = _predictions_ui_url(pid)
            return out

        elapsed = asyncio.get_event_loop().time() - started_at
        if elapsed >= max_wait_seconds:
            result = {
                "prediction_id": prediction_id,
                "status": status or "PENDING",
                "timed_out": True,
            }
            if resolved_project:
                result["ui_url"] = _predictions_ui_url(resolved_project)
            return result
        await asyncio.sleep(poll_interval_seconds)


@mcp.tool(annotations=_READ_ONLY)
async def get_prediction(prediction_id: str) -> dict[str, Any]:
    """
    Fetch prediction status and result by prediction id.

    - Ad-hoc completed: ``output`` contains the label/probability.
    - Batch completed: ``download_url`` and ``curl_command`` are included
      automatically (tokenized gateway proxy, safe from remote sandbox).
    """
    raw = await api_client.get_prediction(prediction_id, **_kw())
    out = _sanitize_prediction(raw)
    project_id = str(raw.get("projectId", "")).strip()
    if project_id:
        out["ui_url"] = _predictions_ui_url(project_id)

    if raw.get("downloadReady"):
        try:
            download_meta = await api_client.get_prediction_download(prediction_id.strip(), **_kw())
            download_url_raw = str(download_meta.get("gatewayDownloadUrl", "")).strip()
            if download_url_raw:
                download_url, download_token = _extract_tokenized_url(download_url_raw, "downloadToken")
                if download_token:
                    out["download_url"] = download_url
                    out["curl_command"] = (
                        f'curl -fsSL -H "X-Download-Token: {download_token}" '
                        f'-o "predictions.csv" "{download_url}"'
                    )
                    expires = download_meta.get("expiresInSeconds")
                    if expires is not None:
                        out["download_expires_in_seconds"] = int(expires)
        except Exception:
            out["download_error"] = "Could not generate download URL; call again to retry"

    return out


@mcp.tool(annotations=_READ_ONLY)
async def list_predictions(project_id: str = "") -> list[dict[str, Any]]:
    """
    List predictions (newest first). Optionally filter by project_id.
    Use ``get_prediction(id)`` for full result + batch download URL.
    """
    raw = await api_client.list_predictions(project_id, **_kw())
    out = [_sanitize_prediction(p) for p in raw]
    for item, source in zip(out, raw):
        pid = str(source.get("projectId", "")).strip()
        if pid:
            item["ui_url"] = _predictions_ui_url(pid)
    return out

# ── Manifest ───────────────────────────────────────────────────────────────────

EDA_MCP_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "get_account_status",
        "list_projects",
        "get_project",
        "create_project",
        "list_datasets",
        "get_dataset",
        "update_dataset",
        "start_upload",
        "upload_from_url",
        "get_upload_status",
        "complete_upload",
        "list_dataset_versions",
        "get_dataset_version",
        "create_dataset_version",
        "create_model",
        "get_model",
        "create_model_version",
        "list_models",
        "list_model_versions",
        "get_model_version",
        "get_model_report",
        "submit_training_job",
        "get_training_status",
        "run_prediction",
        "run_batch_prediction",
        "get_prediction",
        "list_predictions",
    }
)
