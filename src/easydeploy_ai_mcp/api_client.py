"""
api_client.py
Thin async HTTP client for the EasyDeploy REST API.

Import this module when you need to call the EDA API from Python without going through the MCP server.

All functions raise httpx.HTTPStatusError on non-2xx responses.
Callers decide how to handle errors; this module never swallows them.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx

from .defaults import DEFAULT_EDA_API_BASE


@asynccontextmanager
async def _secure_client(**kwargs: object) -> AsyncIterator[httpx.AsyncClient]:
    """Return an httpx client with TLS certificate verification enforced."""
    async with httpx.AsyncClient(verify=True, **kwargs) as client:  # type: ignore[arg-type]
        yield client


def _require_https(url: str, label: str = "URL") -> None:
    """Raise ValueError if a URL is not HTTPS."""
    if not url.startswith("https://"):
        raise ValueError(
            f"Refusing to use non-HTTPS {label}: {url!r}. "
            "Encryption in transit is required."
        )


def normalize_api_base(raw: str) -> str:
    """Strip trailing slashes; append /v1 if not present (matches JS smoke scripts)."""
    b = raw.strip().rstrip("/")
    if b.endswith("/v1"):
        return b
    return f"{b}/v1"


def _require_env() -> tuple[str, str]:
    """Return (api_key, base_url) with base_url normalized to include /v1."""
    try:
        key = os.environ["EDA_API_KEY"]
    except KeyError as e:
        raise RuntimeError(
            f"Missing environment variable: {e.args[0]!r}. "
            "Set EDA_API_KEY (from the EasyDeploy dashboard)."
        ) from e
    raw_base = os.environ.get("EDA_API_BASE", "").strip()
    base = normalize_api_base(raw_base if raw_base else DEFAULT_EDA_API_BASE)
    _require_https(base, "EDA_API_BASE")
    return key, base


def _headers(api_key: str, caller_channel: str = "") -> dict[str, str]:
    h: dict[str, str] = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if caller_channel:
        h["X-Caller-Channel"] = caller_channel
    return h


# ── Account ────────────────────────────────────────────────────────────────────


async def get_account_status(
    customer_id: str,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> dict:
    """
    GET /account — returns tier, credits, predictions, and endpoint limits.
    customer_id is accepted for routing context; the backend resolves the
    account from the authenticated API key.
    """
    async with _secure_client() as client:
        resp = await client.get(
            f"{base_url}/account",
            headers=_headers(api_key, caller_channel),
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def get_account_status_env(customer_id: str = "") -> dict:
    """
    GET /account using EDA_API_KEY from the environment (optional EDA_API_BASE override).
    customer_id is optional; the backend resolves the account from the API key.
    """
    api_key, base_url = _require_env()
    return await get_account_status(customer_id, api_key=api_key, base_url=base_url)


# ── Projects ───────────────────────────────────────────────────────────────────


async def list_projects(
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> list:
    """
    GET /projects — list projects owned by the authenticated API key.
    Response data is an array of { id, name, description, createdAt, updatedAt }.
    """
    async with _secure_client() as client:
        resp = await client.get(
            f"{base_url}/projects",
            headers=_headers(api_key, caller_channel),
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def get_project(
    project_id: str,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> dict:
    """GET /projects/{projectId} — single project if accessible."""
    async with _secure_client() as client:
        resp = await client.get(
            f"{base_url}/projects/{project_id}",
            headers=_headers(api_key, caller_channel),
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def create_project(
    body: dict,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> dict:
    """
    POST /projects — body { name, description? }.
    Returns { id, name, description, organizationId?, createdAt, updatedAt }.
    """
    async with _secure_client() as client:
        resp = await client.post(
            f"{base_url}/projects",
            headers=_headers(api_key, caller_channel),
            json=body,
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def update_project(
    project_id: str,
    body: dict,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> dict:
    """PATCH /projects/{projectId} — body { name?, description? }."""
    async with _secure_client() as client:
        resp = await client.patch(
            f"{base_url}/projects/{project_id}",
            headers=_headers(api_key, caller_channel),
            json=body,
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def create_dataset(
    project_id: str,
    body: dict,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> dict:
    """
    POST /projects/{projectId}/datasets — creates Dataset + first DatasetVersion (raw).
    body: name, s3Key, fileSize required; id?, description?, type? optional.
    """
    async with _secure_client() as client:
        resp = await client.post(
            f"{base_url}/projects/{project_id}/datasets",
            headers=_headers(api_key, caller_channel),
            json=body,
            timeout=120.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def complete_dataset_upload(
    project_id: str,
    body: dict,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> dict:
    """
    POST /projects/{projectId}/datasets/complete-upload
    body: { uploadRequestId, name, datasetId?, datasetType?, description? }
    """
    async with _secure_client() as client:
        resp = await client.post(
            f"{base_url}/projects/{project_id}/datasets/complete-upload",
            headers=_headers(api_key, caller_channel),
            json=body,
            timeout=120.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def list_datasets(
    project_id: str,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> list:
    """GET /projects/{projectId}/datasets"""
    async with _secure_client() as client:
        resp = await client.get(
            f"{base_url}/projects/{project_id}/datasets",
            headers=_headers(api_key, caller_channel),
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def get_dataset(
    project_id: str,
    dataset_id: str,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> dict:
    """GET /projects/{projectId}/datasets/{datasetId}"""
    async with _secure_client() as client:
        resp = await client.get(
            f"{base_url}/projects/{project_id}/datasets/{dataset_id}",
            headers=_headers(api_key, caller_channel),
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def update_dataset(
    project_id: str,
    dataset_id: str,
    body: dict,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> dict:
    """PATCH /projects/{projectId}/datasets/{datasetId} — body { name?, description? }."""
    async with _secure_client() as client:
        resp = await client.patch(
            f"{base_url}/projects/{project_id}/datasets/{dataset_id}",
            headers=_headers(api_key, caller_channel),
            json=body,
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]


# ── Dataset versions ───────────────────────────────────────────────────────────


async def create_dataset_version(
    project_id: str,
    dataset_id: str,
    body: dict,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> dict:
    """
    POST /projects/{projectId}/datasets/{datasetId}/versions
    body keys: s3Key, fileSize, version_type, qa_metadata (optional)
    Returns the full response data dict.
    """
    async with _secure_client() as client:
        resp = await client.post(
            f"{base_url}/projects/{project_id}/datasets/{dataset_id}/versions",
            headers=_headers(api_key, caller_channel),
            json=body,
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def list_dataset_versions(
    dataset_id: str,
    project_id: str = "",
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> list:
    """GET dataset versions. Uses flat ``/datasets/{datasetId}/versions`` when project_id is empty."""
    path = (
        f"{base_url}/projects/{project_id.strip()}/datasets/{dataset_id}/versions"
        if project_id.strip()
        else f"{base_url}/datasets/{dataset_id}/versions"
    )
    async with _secure_client() as client:
        resp = await client.get(
            path,
            headers=_headers(api_key, caller_channel),
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def get_dataset_version(
    project_id: str,
    dataset_id: str,
    version_id: str,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> dict:
    """GET /projects/{projectId}/datasets/{datasetId}/versions/{versionId}"""
    async with _secure_client() as client:
        resp = await client.get(
            f"{base_url}/projects/{project_id}/datasets/{dataset_id}/versions/{version_id}",
            headers=_headers(api_key, caller_channel),
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def patch_dataset_version(
    project_id: str,
    dataset_id: str,
    version_id: str,
    body: dict,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> dict:
    """
    PATCH /projects/{projectId}/datasets/{datasetId}/versions/{versionId}
    body keys: qa_status
    Returns the updated version object.
    """
    async with _secure_client() as client:
        resp = await client.patch(
            f"{base_url}/projects/{project_id}/datasets/{dataset_id}/versions/{version_id}",
            headers=_headers(api_key, caller_channel),
            json=body,
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def get_presigned_upload_url(
    filename: str,
    project_id: str,
    *,
    api_key: str,
    base_url: str,
    dataset_id: str | None = None,
    caller_channel: str = "",
) -> dict:
    """
    POST /uploads/url — returns gatewayUploadUrl + uploadRequestId for API Gateway upload.
    Omit dataset_id for a new dataset (API generates an id — use it as body.id when creating the dataset).
    Pass dataset_id to add a version under an existing dataset path.
    """
    payload: dict[str, str] = {"filename": filename, "projectId": project_id}
    if dataset_id:
        payload["datasetId"] = dataset_id
    async with _secure_client() as client:
        resp = await client.post(
            f"{base_url}/uploads/url",
            headers=_headers(api_key, caller_channel),
            json=payload,
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def upload_to_s3(
    upload_url: str,
    file_bytes: bytes,
) -> None:
    """PUT file bytes directly to a HTTPS upload URL (gateway upload URL supported)."""
    _require_https(upload_url, "upload URL")
    async with _secure_client() as client:
        resp = await client.put(
            upload_url,
            content=file_bytes,
            headers={
                "Content-Type": "text/csv",
            },
            timeout=120.0,
        )
        resp.raise_for_status()


# ── Training ───────────────────────────────────────────────────────────────────


async def submit_training_job(
    body: dict,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> dict:
    """
    POST /training-jobs
    body keys: modelVersionId (required), plus optional datasetVersionId or fileUrl.
    Returns the response data dict.
    """
    async with _secure_client() as client:
        resp = await client.post(
            f"{base_url}/training-jobs",
            headers=_headers(api_key, caller_channel),
            json=body,
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def get_training_status(
    job_id: str,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> dict:
    """GET /training-jobs/{jobId}

    Response ``data`` includes:
      - ``status``: PENDING | RUNNING | COMPLETE | FAILED (stable for agents)
      - ``batchStatus``: raw AWS Batch status (e.g. SUCCEEDED, RUNNING)
      - ``trainingTimeSeconds``: wall-clock training seconds once the job has stopped; null while running
      - ``modelVersionId``: from the job environment when present
    """
    async with _secure_client() as client:
        resp = await client.get(
            f"{base_url}/training-jobs/{job_id}",
            headers=_headers(api_key, caller_channel),
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]


# ── Predictions ────────────────────────────────────────────────────────────────


async def run_prediction(
    body: dict,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> dict:
    """
    POST /predictions
    body keys: projectId, modelVersionId, targetFeature, input
    Returns the response data dict.
    """
    async with _secure_client() as client:
        resp = await client.post(
            f"{base_url}/predictions",
            headers=_headers(api_key, caller_channel),
            json=body,
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def get_prediction(
    prediction_id: str,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> dict:
    """GET /predictions/{predictionId}"""
    async with _secure_client() as client:
        resp = await client.get(
            f"{base_url}/predictions/{prediction_id}",
            headers=_headers(api_key, caller_channel),
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def get_prediction_download(
    prediction_id: str,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> dict:
    """GET /predictions/{predictionId}/download"""
    async with _secure_client() as client:
        resp = await client.get(
            f"{base_url}/predictions/{prediction_id}/download",
            headers=_headers(api_key, caller_channel),
            timeout=20.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def list_predictions(
    project_id: str = "",
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> list[dict]:
    """GET /predictions?projectId=... (project_id optional)."""
    params = {"projectId": project_id} if project_id.strip() else None
    async with _secure_client() as client:
        resp = await client.get(
            f"{base_url}/predictions",
            headers=_headers(api_key, caller_channel),
            params=params,
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]


# ── Models ─────────────────────────────────────────────────────────────────────


async def list_models(
    project_id: str,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> list:
    """GET /projects/{projectId}/models"""
    async with _secure_client() as client:
        resp = await client.get(
            f"{base_url}/projects/{project_id}/models",
            headers=_headers(api_key, caller_channel),
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def list_model_versions(
    project_id: str,
    model_id: str,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> list:
    """GET /projects/{projectId}/models/{modelId}/versions"""
    async with _secure_client() as client:
        resp = await client.get(
            f"{base_url}/projects/{project_id}/models/{model_id}/versions",
            headers=_headers(api_key, caller_channel),
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def get_model(
    project_id: str,
    model_id: str,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> dict:
    """GET /projects/{projectId}/models/{modelId}"""
    async with _secure_client() as client:
        resp = await client.get(
            f"{base_url}/projects/{project_id}/models/{model_id}",
            headers=_headers(api_key, caller_channel),
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def get_model_by_id(
    model_id: str,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> dict:
    """GET /models/{modelId} — same payload as nested GET; project resolved server-side."""
    async with _secure_client() as client:
        resp = await client.get(
            f"{base_url}/models/{model_id}",
            headers=_headers(api_key, caller_channel),
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def update_model(
    project_id: str,
    model_id: str,
    body: dict,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> dict:
    """PATCH /projects/{projectId}/models/{modelId} — body { name?, description? }."""
    async with _secure_client() as client:
        resp = await client.patch(
            f"{base_url}/projects/{project_id}/models/{model_id}",
            headers=_headers(api_key, caller_channel),
            json=body,
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def get_model_version(
    project_id: str,
    model_id: str,
    version_id: str,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> dict:
    """GET /projects/{projectId}/models/{modelId}/versions/{versionId}"""
    async with _secure_client() as client:
        resp = await client.get(
            f"{base_url}/projects/{project_id}/models/{model_id}/versions/{version_id}",
            headers=_headers(api_key, caller_channel),
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def create_model(
    project_id: str,
    body: dict,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> dict:
    """POST /projects/{projectId}/models — body { name, description? }."""
    async with _secure_client() as client:
        resp = await client.post(
            f"{base_url}/projects/{project_id}/models",
            headers=_headers(api_key, caller_channel),
            json=body,
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def create_model_version(
    project_id: str,
    model_id: str,
    body: dict,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> dict:
    """
    POST /projects/{projectId}/models/{modelId}/versions
    body: { datasetVersionId, targetFeature }
    """
    async with _secure_client() as client:
        resp = await client.post(
            f"{base_url}/projects/{project_id}/models/{model_id}/versions",
            headers=_headers(api_key, caller_channel),
            json=body,
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def get_model_report(
    model_id: str,
    project_id: str = "",
    model_version_id: str = "",
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
    report_scope: str = "summary",
) -> dict:
    """GET EDA report JSON from S3 (summary or full scope).

    Use ``GET /models/{modelId}/report`` when ``project_id`` is empty; otherwise the nested
    ``/projects/{projectId}/models/{modelId}/report`` URL is used.

    Metrics and narrative live in **S3** ``eda_model_report.json`` (written after training by
    ``generate_eda_report``), not on the DynamoDB Model row.

    ``model_version_id``: omit or empty to use the **latest** model version (by version number).
    Pass a specific id from ``list_model_versions`` to pin a version.

    ``report_scope``: ``summary`` (default) = token-efficient ``summary`` block only; ``full`` = entire JSON.
    The returned dict may include ``_resolvedModelVersionId`` and ``_reportScope`` from API meta.
    """
    scope = report_scope.strip().lower() if report_scope else "summary"
    if scope not in ("summary", "full"):
        scope = "summary"
    params: dict[str, str] = {"scope": scope}
    if model_version_id.strip():
        params["versionId"] = model_version_id.strip()
    path = (
        f"{base_url}/projects/{project_id.strip()}/models/{model_id}/report"
        if project_id.strip()
        else f"{base_url}/models/{model_id}/report"
    )
    async with _secure_client() as client:
        resp = await client.get(
            path,
            headers=_headers(api_key, caller_channel),
            params=params,
            timeout=60.0,
        )
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("data")
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        if isinstance(data, dict):
            out = dict(data)
            if meta.get("modelVersionId") is not None:
                out["_resolvedModelVersionId"] = str(meta["modelVersionId"])
            if meta.get("reportScope") is not None:
                out["_reportScope"] = str(meta["reportScope"])
            return out
        return data if data is not None else {}


async def deploy_endpoint(
    project_id: str,
    model_id: str,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> dict:
    """POST /projects/{projectId}/models/{modelId}/endpoints"""
    async with _secure_client() as client:
        resp = await client.post(
            f"{base_url}/projects/{project_id}/models/{model_id}/endpoints",
            headers=_headers(api_key, caller_channel),
            json={},
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()["data"]
