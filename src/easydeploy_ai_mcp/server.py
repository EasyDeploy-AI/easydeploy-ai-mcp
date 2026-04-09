"""
easydeploy_ai_mcp.server
EasyDeploy AI MCP server — REST API wrapper (FastMCP name: **EasyDeploy AI**).

Tool definitions follow the EasyDeploy public REST API; keep them aligned when the API surface changes.

Local stdio: run ``easydeploy-ai-mcp-stdio`` (or ``python -m easydeploy_ai_mcp``) with env
``EDA_API_KEY`` set (from the dashboard). Optional ``EDA_API_BASE`` overrides the production API host for internal use only.

Remote HTTP: run ``easydeploy-ai-mcp-http`` or uvicorn ``easydeploy_ai_mcp.http_main:app``; see README and docs/aws-p0.md.

If the client lists fewer tools than expected (for example **start_upload** or **get_training_status** missing),
fully quit and restart the MCP host (Claude Desktop, Cursor, or your connector), confirm the configured
command or image points at the current install, and pull the latest package. Verify locally:

    pytest tests/test_server_tools.py::test_eda_mcp_registered_tools_match_manifest -q

Security:
  - All API calls enforce HTTPS (TLS) — non-HTTPS URLs are rejected.
  - Response sanitization strips internal storage paths and auth fields.
  - MCP stdio transport is a local process pipe — never traverses a network.
  - HTTP transport: use TLS in production; optional ``MCP_SERVICE_TOKEN`` gates the MCP app (not ``/healthz``).

Upload paths:

  From chat attachment / remote sandbox (recommended for claude.ai):
    1. start_upload → returns upload_request_id + curl command
    2. Run curl_command in bash (max ~6 MB per file until multipart ships)
    3. complete_upload with project_id, name, upload_request_id (+ optional dataset_id)

Tool catalog (24 tools — restart MCP after edits):
  Account: get_account_status
  Projects: list_projects, get_project, create_project (pass project_id to update)
  Datasets: list_datasets, get_dataset (pass name/description to update), start_upload,
    complete_upload
  Dataset versions: list_dataset_versions (project_id optional), get_dataset_version,
    create_dataset_version (pass version_id to update qa_status)
  Models: create_model (pass model_id to update), get_model, create_model_version,
    list_models, list_model_versions, get_model_version,
    get_model_report (project_id optional — inferred from model_id)
  Training: submit_training_job, get_training_status (if missing from host, poll list_model_versions)
  Predictions: run_prediction, run_batch_prediction (project_id + target_feature auto-resolved),
    get_prediction (includes batch download URL when ready), list_predictions
"""

from __future__ import annotations

import asyncio
import os
import sys

import httpx
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from dotenv import load_dotenv
from fastmcp import FastMCP

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
        "Use an HTTPS URL with a hostname (e.g. https://api.easydeploy.ai or sandbox execute-api …/prod)."
    )
_API_KEY: str = os.environ.get("EDA_API_KEY", "")
_UI_BASE_URL: str = os.environ.get("EDA_UI_BASE_URL", "https://easydeploy.ai").rstrip("/")

mcp = FastMCP("EasyDeploy AI")


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


# ── Account ────────────────────────────────────────────────────────────────────


@mcp.tool()
async def get_account_status(customer_id: str = "") -> dict[str, Any]:
    """
    Get current account status: tier, training credits, prediction usage, endpoint limits.
    customer_id is optional; the backend resolves the account from the API key.
    """
    return await api_client.get_account_status(customer_id, **_kw())


# ── Projects ───────────────────────────────────────────────────────────────────


@mcp.tool()
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


@mcp.tool()
async def get_project(project_id: str) -> dict[str, Any]:
    """Fetch a single project by id."""
    data = await api_client.get_project(project_id, **_kw())
    pid = str(data.get("id", "")).strip() or project_id.strip()
    if pid:
        data["ui_url"] = _project_ui_url(pid)
    return data


@mcp.tool()
async def create_project(
    name: str,
    description: str = "",
    project_id: str = "",
) -> dict[str, Any]:
    """
    Create or update a project.

    - **Create**: call with ``name`` (and optional ``description``).
    - **Update/rename**: pass ``project_id`` of an existing project plus the
      fields to change (``name`` and/or ``description``).
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


@mcp.tool()
async def list_datasets(project_id: str) -> list[dict[str, Any]]:
    """List datasets in a project (id, name, type, timestamps)."""
    datasets = await api_client.list_datasets(project_id, **_kw())
    for item in datasets:
        did = str(item.get("id", "")).strip()
        if did:
            item["ui_url"] = _dataset_ui_url(project_id, did)
    return datasets


@mcp.tool()
async def get_dataset(
    project_id: str,
    dataset_id: str,
    name: str = "",
    description: str = "",
) -> dict[str, Any]:
    """
    Fetch or update a dataset.

    - **Read**: call with ``project_id`` and ``dataset_id`` only.
    - **Update/rename**: also pass ``name`` and/or ``description`` to change.

    Datasets are *created* via ``complete_upload`` (the upload flow); use this
    tool for reads and metadata edits.
    """
    if name.strip() or description.strip():
        body: dict[str, str] = {}
        if name.strip():
            body["name"] = name.strip()
        if description.strip():
            body["description"] = description.strip()
        data = await api_client.update_dataset(project_id, dataset_id, body, **_kw())
    else:
        data = await api_client.get_dataset(project_id, dataset_id, **_kw())
    did = str(data.get("id", "")).strip() or dataset_id.strip()
    if did:
        data["ui_url"] = _dataset_ui_url(project_id, did)
    return data


@mcp.tool()
async def start_upload(
    filename: str,
    project_id: str,
    dataset_id: str = "",
) -> dict[str, Any]:
    """
    Start an upload request and return a gateway upload curl command.

    FULL 3-STEP FLOW:

    Step 1 — call start_upload.
    Step 2 — run curl_command in bash.
      Replace FILE_PATH with the actual file path.
    Step 3 — call complete_upload with upload_request_id from step 1.

    No API key or auth header is needed in the curl command.
    Pass dataset_id when uploading a new version of an existing dataset.
    """
    ds = dataset_id.strip() or None
    data = await api_client.get_presigned_upload_url(
        filename, project_id, **_kw(), dataset_id=ds,
    )
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
    data["next_steps"] = (
        "1. Replace FILE_PATH in curl_command with the actual file path and run it. "
        f"2. Call complete_upload with project_id='{project_id}', "
        f"upload_request_id='{data.get('uploadRequestId', '')}', "
        f"dataset_id='{data.get('datasetId', '')}', and your dataset name."
    )

    data["upload_request_id"] = str(data.get("uploadRequestId", ""))
    data.pop("uploadRequestId", None)
    data.pop("bucket", None)
    data.pop("fileUrl", None)
    data.pop("s3Key", None)
    data.pop("gatewayUploadUrl", None)
    data.pop("uploadUrl", None)

    return data


@mcp.tool()
async def complete_upload(
    project_id: str,
    name: str,
    upload_request_id: str,
    description: str = "",
    dataset_type: str = "train",
    dataset_id: str = "",
) -> dict[str, Any]:
    """
    Finalize an upload after start_upload + curl.
    upload_request_id: opaque id returned by start_upload.
    dataset_id: optional target dataset id for creating a new version.
      If the dataset already exists, a new version is created automatically.
    dataset_type: train | test | validation (default train).

    Returns the dataset record with id, name, and the new datasetVersion.
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


@mcp.tool()
async def list_dataset_versions(dataset_id: str, project_id: str = "") -> list[dict[str, Any]]:
    """
    List all versions of a dataset (version number, version_type, qa_status, row counts).

    ``project_id`` is optional — the backend resolves access from ``dataset_id`` when omitted.
    """
    versions = await api_client.list_dataset_versions(dataset_id, project_id.strip(), **_kw())
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


@mcp.tool()
async def get_dataset_version(project_id: str, dataset_id: str, version_id: str) -> dict[str, Any]:
    """Fetch one dataset version by id (metadata, qa_status, version_type)."""
    item = await api_client.get_dataset_version(project_id, dataset_id, version_id, **_kw())
    base = _dataset_ui_url(project_id, dataset_id)
    version = item.get("version") if isinstance(item, dict) else None
    if isinstance(item, dict):
        item["ui_url"] = f"{base}?version={version}" if version is not None else base
    return item


@mcp.tool()
async def create_dataset_version(
    project_id: str,
    dataset_id: str,
    version_type: str = "",
    file_url: str = "",
    qa_metadata: dict[str, Any] | None = None,
    version_id: str = "",
    qa_status: str = "",
) -> dict[str, Any]:
    """
    Create or update a dataset version.

    **Create** (register an S3 file as a new version — used by the QA pipeline):
      Required: ``version_type`` (raw | qa_cleaned | training), ``file_url`` (s3:// URL),
      ``qa_metadata`` (freeform JSON with QA results).

    **Update** (change qa_status on an existing version):
      Required: ``version_id``, ``qa_status`` (pending | in_progress | ready | blocked).
    """
    vid = version_id.strip()
    if vid:
        qs = qa_status.strip()
        if not qs:
            raise ValueError("qa_status is required when updating a dataset version")
        out = await api_client.patch_dataset_version(
            project_id, dataset_id, vid, {"qa_status": qs}, **_kw()
        )
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
    base = _dataset_ui_url(project_id, dataset_id)
    dataset_version = out.get("datasetVersion") if isinstance(out, dict) else None
    if isinstance(dataset_version, dict):
        version = dataset_version.get("version")
        dataset_version["ui_url"] = f"{base}?version={version}" if version is not None else base
    return out


# ── Models ─────────────────────────────────────────────────────────────────────


@mcp.tool()
async def create_model(
    project_id: str,
    name: str,
    description: str = "",
    model_id: str = "",
) -> dict[str, Any]:
    """
    Create or update a model.

    - **Create**: call with ``project_id`` and ``name`` (and optional ``description``).
    - **Update/rename**: also pass ``model_id`` plus the fields to change.
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


@mcp.tool()
async def get_model(project_id: str, model_id: str) -> dict[str, Any]:
    """Fetch a single model by id (name, description, version count)."""
    data = await api_client.get_model(project_id, model_id, **_kw())
    mid = str(data.get("id", "")).strip() or model_id.strip()
    if mid:
        data["ui_url"] = _model_ui_url(project_id, mid)
    return data


@mcp.tool()
async def create_model_version(
    project_id: str,
    model_id: str,
    dataset_version_id: str,
    target_feature: str,
) -> dict[str, Any]:
    """
    Create a model version tied to a dataset version and target column.
    Then call submit_training_job with the returned model version id.
    """
    body = {
        "datasetVersionId": dataset_version_id,
        "targetFeature": target_feature.strip(),
    }
    out = await api_client.create_model_version(project_id, model_id, body, **_kw())
    if isinstance(out, dict):
        out["ui_url"] = _model_ui_url(project_id, model_id)
    return out


@mcp.tool()
async def list_models(project_id: str) -> list[dict[str, Any]]:
    """List all models in a project (id, name)."""
    models = await api_client.list_models(project_id, **_kw())
    for item in models:
        mid = str(item.get("id", "")).strip()
        if mid:
            item["ui_url"] = _model_ui_url(project_id, mid)
    return models


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
async def submit_training_job(
    model_version_id: str,
    dataset_version_id: str = "",
) -> dict[str, Any]:
    """
    Submit a training job for a model version.

    **Track completion:** Poll ``list_model_versions`` for the model and watch the target
    version's ``status`` until it reaches TRAINING_COMPLETED or TRAINING_FAILED (same as the
    web UI). This is the most reliable approach across MCP hosts.

    If your integration exposes ``get_training_status``, you can instead pass the returned
    ``jobId`` with ``wait=true`` to block until the Batch job finishes (typical 2–3 min).
    Some hosts omit that tool when running stale server code — use ``list_model_versions`` then.

    **dataset_version_id** can be omitted when the model version was created with
    ``create_model_version`` in the same flow — the backend resolves target_feature,
    file, and dataset from the model version record automatically. Pass it only to
    override with a *different* qa_cleaned/training dataset version.

    Returns ``{jobId, modelVersionId, status}``.
    """
    body: dict = {"modelVersionId": model_version_id}
    if dataset_version_id:
        body["datasetVersionId"] = dataset_version_id
    return await api_client.submit_training_job(body, **_kw())


@mcp.tool()
async def get_training_status(
    job_id: str,
    wait: bool = False,
    timeout_seconds: int = 180,
    poll_interval_seconds: float = 10.0,
) -> dict[str, Any]:
    """
    Check a training job by **job_id** (the ``jobId`` field from ``submit_training_job``).

    **If this tool does not appear in your MCP tool list:** restart the host and ensure
    the client runs current ``easydeploy_ai_mcp`` (standard catalog is 24 tools).
    Until then, poll ``list_model_versions`` for the model version ``status`` instead.

    Response fields:
      - status: PENDING | RUNNING | COMPLETE | FAILED
      - trainingTimeSeconds: wall-clock seconds once the job stops; null while running
      - modelVersionId: the model version being trained

    By default returns the current status immediately.

    Set **wait=true** to block until the job reaches a terminal state (COMPLETE or
    FAILED). Polls every ``poll_interval_seconds`` (default 10 s) for up to
    ``timeout_seconds`` (default 180 s / 3 min). Typical training runs finish in
    2-3 minutes. If the timeout expires, the last polled status is returned with
    ``timed_out: true``.
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


@mcp.tool()
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


@mcp.tool()
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
    the row count for credit billing.

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


@mcp.tool()
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


@mcp.tool()
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
        "start_upload",
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
