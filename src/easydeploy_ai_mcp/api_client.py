"""
api_client.py
Thin async HTTP client for the EasyDeploy REST API.

Import this module when you need to call the EDA API from Python without going through the MCP server.

All functions raise EasyDeployAPIError (an httpx.HTTPStatusError) on non-2xx
responses, with a message that says what failed and why (see _raise_for_status),
and ValueError before any request when an id argument is not a well-formed id
(see require_id). Callers decide how to handle errors; this module never
swallows them.
"""

from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from itertools import pairwise
from typing import AsyncIterator
from urllib.parse import quote, unquote

import httpx

from .defaults import DEFAULT_EDA_API_BASE

# ── Errors ─────────────────────────────────────────────────────────────────────
#
# An error's message is all an agent sees of it: FastMCP hands str(exc) to the
# model as the tool error. So every non-2xx response becomes an
# EasyDeployAPIError naming the method, the path, the status and the API's own
# code and message, or a short explanation of the status when the body has none.
# Secrets stay out: the query string (where upload and download tokens travel)
# is never shown, and bearer tokens, API keys, JWTs and URL queries quoted in a
# response body are redacted.


class EasyDeployAPIError(httpx.HTTPStatusError):
    """A non-2xx EasyDeploy API response, with a message an agent can act on.

    Subclasses ``httpx.HTTPStatusError``, so ``except httpx.HTTPStatusError``
    and ``exc.response.status_code`` keep working. ``str(exc)`` is the message.
    """


_MAX_DETAIL_CHARS = 500

_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    # A URL's query: presigned signatures and upload/download tokens live there.
    (re.compile(r"(https?://[^\s?#\"'<>]+)\?[^\s\"'<>]*"), r"\1?[redacted]"),
    (re.compile(r"(?i)\b((?:upload|download)token)=[^&\s\"'<>]+"), r"\1=[redacted]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [redacted]"),
    (re.compile(r"\beda_live_[A-Za-z0-9_-]+"), "eda_live_[redacted]"),
    # JWTs, such as Cognito access tokens.
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*"), "[redacted token]"),
)


def _redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def _eda_api_error_detail(response: httpx.Response) -> str:
    """The API's own explanation of an error response, or ``""`` when it gives none.

    Reads the EasyDeploy shape ``{"error": {"code", "message", "requestId"}}``,
    ``{"error": "..."}``, and the ``message`` or ``detail`` that API Gateway and
    proxies send. A plain-text body counts; an empty or HTML body does not.
    """
    try:
        body = response.json()
    except ValueError:
        body = None
    parts: list[str] = []
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            parts = [str(err[k]) for k in ("code", "message") if err.get(k)]
            if err.get("requestId"):
                parts.append(f"request id {err['requestId']}")
        elif isinstance(err, str) and err.strip():
            parts = [err]
        if not parts:
            parts = [
                str(body[k]) for k in ("message", "Message", "detail")
                if isinstance(body.get(k), str) and body[k].strip()
            ][:1]
    elif body is None:
        text = response.text
        if not text.lstrip().startswith("<"):
            parts = [text]
    detail = " | ".join(" ".join(p.split()) for p in parts if p.strip())
    detail = _redact(detail)
    if len(detail) > _MAX_DETAIL_CHARS:
        detail = detail[: _MAX_DETAIL_CHARS - 1] + "…"
    return detail


#: Path collection → the tool argument its id comes from, for 404 messages.
_PATH_ID_NAMES = {
    "projects": "project_id",
    "datasets": "dataset_id",
    "versions": "version_id",
    "models": "model_id",
    "training-jobs": "job_id",
    "predictions": "prediction_id",
    "uploads": "upload_request_id",
}
#: Path segments after a collection that name an action, not an id.
_PATH_ACTIONS = frozenset(
    {"url", "from-url", "data", "complete-upload", "download", "report", "endpoints"}
)


def _ids_in_path(path: str) -> dict[str, str]:
    segments = [s for s in path.split("/") if s]
    ids: dict[str, str] = {}
    for collection, value in pairwise(segments):
        name = _PATH_ID_NAMES.get(collection)
        if name and value not in _PATH_ID_NAMES and value not in _PATH_ACTIONS:
            ids[name] = unquote(value)
    return ids


def _status_explanation(status: int, ids: dict[str, str]) -> str:
    """What a status means for the caller, when the API body does not say."""
    if status in (400, 422):
        return "The API rejected the request as invalid; check the arguments."
    if status == 401:
        return (
            "Not authenticated: the sign-in has expired or the API key is invalid. "
            "Reconnect EasyDeploy (or set a valid EDA_API_KEY) and retry."
        )
    if status == 403:
        return (
            "Not allowed for this account or plan, or the record belongs to another "
            "account."
        )
    if status == 404:
        named = [f"{name} {value[:40]!r}" for name, value in ids.items()]
        if not named:
            what = "An id in the request does"
        elif len(named) == 1:
            what = f"{named[0]} does"
        else:
            what = f"One of {', '.join(named)} does"
        return (
            f"Not found: {what} not exist or is not visible to this account. Use ids "
            "returned by the EasyDeploy list and get tools."
        )
    if status == 409:
        return "Conflict with the record's current state; check its status before retrying."
    if status == 413:
        return (
            "Too large for the API. The upload gateway takes files up to 6 MB; larger "
            "files go by URL with upload_from_url (up to 256 MB)."
        )
    if status == 429:
        return "Rate limited by the EasyDeploy API; wait a few seconds and retry."
    if status >= 500:
        return "EasyDeploy API error on the server side; retry in a few seconds."
    return "Unexpected response from the EasyDeploy API."


#: Bodies that only restate the status, as API Gateway sends for its own errors.
_GENERIC_DETAILS = frozenset({
    "bad request", "unauthorized", "forbidden", "not found", "too many requests",
    "internal server error", "bad gateway", "service unavailable",
})


def _is_generic(detail: str, reason: str) -> bool:
    text = detail.strip(" .").lower()
    return not text or text in _GENERIC_DETAILS or text == reason.strip().lower()


def _raise_for_status(
    response: httpx.Response, *, ids: dict[str, object] | None = None
) -> None:
    """Raise EasyDeployAPIError unless ``response`` is 2xx.

    ``ids`` names ids sent in the body or query, so a 404 can say which ones were
    looked up; ids in the path are found on their own.
    """
    if response.is_success:
        return
    request = response.request
    path = request.url.path  # Never the query: upload and download tokens ride there.
    named = _ids_in_path(path)
    for name, value in (ids or {}).items():
        if value and name not in named:
            named[name] = str(value)
    status = response.status_code
    detail = _eda_api_error_detail(response)
    explanation = _status_explanation(status, named)
    if _is_generic(detail, response.reason_phrase):
        detail = explanation
    elif status == 401:
        # Only the user can fix sign-in, whatever the API calls the failure.
        detail = f"{detail}. {explanation}"
    message = (
        f"EasyDeploy API returned {status} {response.reason_phrase} for "
        f"{request.method} {path}: {detail}"
    )
    raise EasyDeployAPIError(message, request=request, response=response)


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


# Every id the API issues is a UUID (optionally prefixed, e.g. ``pred_<uuid>``) or
# an AWS Batch job id, so ids never need anything outside this set. Keeping ``/``,
# ``.``, ``?``, ``#`` and ``%`` out stops an id argument such as ``../api-keys#``
# from steering a request to a different route.
_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")


def require_id(value: object, name: str) -> str:
    """Return ``value`` ready to use as one URL path segment, or raise ValueError.

    Accepts only 1-128 characters of letters, digits, ``_`` and ``-`` (whole
    string, no surrounding whitespace). The result is also percent-encoded,
    which is a no-op for accepted ids and keeps the path safe if the pattern
    is ever widened. ``name`` is the argument name used in the error message.
    """
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        shown = value[:40] + "..." if isinstance(value, str) and len(value) > 40 else value
        raise ValueError(
            f"{name} must be 1-128 letters, digits, '-' or '_' "
            f"(an id returned by the EasyDeploy API); got {shown!r}"
        )
    return quote(value, safe="")


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
        _raise_for_status(resp)
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
        _raise_for_status(resp)
        return resp.json()["data"]


async def get_project(
    project_id: str,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> dict:
    """GET /projects/{projectId} — single project if accessible."""
    url = f"{base_url}/projects/{require_id(project_id, 'project_id')}"
    async with _secure_client() as client:
        resp = await client.get(
            url,
            headers=_headers(api_key, caller_channel),
            timeout=15.0,
        )
        _raise_for_status(resp)
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
        _raise_for_status(resp)
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
    url = f"{base_url}/projects/{require_id(project_id, 'project_id')}"
    async with _secure_client() as client:
        resp = await client.patch(
            url,
            headers=_headers(api_key, caller_channel),
            json=body,
            timeout=15.0,
        )
        _raise_for_status(resp)
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
    url = f"{base_url}/projects/{require_id(project_id, 'project_id')}/datasets"
    async with _secure_client() as client:
        resp = await client.post(
            url,
            headers=_headers(api_key, caller_channel),
            json=body,
            timeout=120.0,
        )
        _raise_for_status(resp)
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
    url = f"{base_url}/projects/{require_id(project_id, 'project_id')}/datasets/complete-upload"
    async with _secure_client() as client:
        resp = await client.post(
            url,
            headers=_headers(api_key, caller_channel),
            json=body,
            timeout=120.0,
        )
        _raise_for_status(resp, ids={
            "upload_request_id": body.get("uploadRequestId"),
            "dataset_id": body.get("datasetId"),
        })
        return resp.json()["data"]


async def list_datasets(
    project_id: str,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> list:
    """GET /projects/{projectId}/datasets"""
    url = f"{base_url}/projects/{require_id(project_id, 'project_id')}/datasets"
    async with _secure_client() as client:
        resp = await client.get(
            url,
            headers=_headers(api_key, caller_channel),
            timeout=30.0,
        )
        _raise_for_status(resp)
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
    url = (
        f"{base_url}/projects/{require_id(project_id, 'project_id')}"
        f"/datasets/{require_id(dataset_id, 'dataset_id')}"
    )
    async with _secure_client() as client:
        resp = await client.get(
            url,
            headers=_headers(api_key, caller_channel),
            timeout=15.0,
        )
        _raise_for_status(resp)
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
    url = (
        f"{base_url}/projects/{require_id(project_id, 'project_id')}"
        f"/datasets/{require_id(dataset_id, 'dataset_id')}"
    )
    async with _secure_client() as client:
        resp = await client.patch(
            url,
            headers=_headers(api_key, caller_channel),
            json=body,
            timeout=15.0,
        )
        _raise_for_status(resp)
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
    url = (
        f"{base_url}/projects/{require_id(project_id, 'project_id')}"
        f"/datasets/{require_id(dataset_id, 'dataset_id')}/versions"
    )
    async with _secure_client() as client:
        resp = await client.post(
            url,
            headers=_headers(api_key, caller_channel),
            json=body,
            timeout=15.0,
        )
        _raise_for_status(resp)
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
    did = require_id(dataset_id, "dataset_id")
    path = (
        f"{base_url}/projects/{require_id(project_id.strip(), 'project_id')}/datasets/{did}/versions"
        if project_id.strip()
        else f"{base_url}/datasets/{did}/versions"
    )
    async with _secure_client() as client:
        resp = await client.get(
            path,
            headers=_headers(api_key, caller_channel),
            timeout=30.0,
        )
        _raise_for_status(resp)
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
    url = (
        f"{base_url}/projects/{require_id(project_id, 'project_id')}"
        f"/datasets/{require_id(dataset_id, 'dataset_id')}"
        f"/versions/{require_id(version_id, 'version_id')}"
    )
    async with _secure_client() as client:
        resp = await client.get(
            url,
            headers=_headers(api_key, caller_channel),
            timeout=15.0,
        )
        _raise_for_status(resp)
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
    url = (
        f"{base_url}/projects/{require_id(project_id, 'project_id')}"
        f"/datasets/{require_id(dataset_id, 'dataset_id')}"
        f"/versions/{require_id(version_id, 'version_id')}"
    )
    async with _secure_client() as client:
        resp = await client.patch(
            url,
            headers=_headers(api_key, caller_channel),
            json=body,
            timeout=10.0,
        )
        _raise_for_status(resp)
        return resp.json()["data"]


async def get_upload_url(
    filename: str,
    project_id: str,
    *,
    api_key: str,
    base_url: str,
    dataset_id: str | None = None,
    caller_channel: str = "",
) -> dict:
    """
    Request a gateway upload URL for a dataset file.

    Calls POST /uploads/url and returns a gatewayUploadUrl for a direct PUT
    to the API Gateway. Note: this is NOT a presigned S3 URL. See architecture
    docs for rationale.

    The response also carries ``channels`` (``gateway``, ``fromUrl``, ``status``)
    and a ``fallback`` string describing what to do when no channel works from
    the caller's sandbox.
    """
    # Not path segments, but the API builds the upload's S3 key from both ids.
    payload: dict[str, str] = {
        "filename": filename,
        "projectId": require_id(project_id, "project_id"),
    }
    if dataset_id:
        payload["datasetId"] = require_id(dataset_id, "dataset_id")
    async with _secure_client() as client:
        resp = await client.post(
            f"{base_url}/uploads/url",
            headers=_headers(api_key, caller_channel),
            json=payload,
            timeout=15.0,
        )
        _raise_for_status(resp, ids={"project_id": project_id, "dataset_id": dataset_id})
        return resp.json()["data"]


async def upload_from_url(
    upload_request_id: str,
    source_url: str,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> dict:
    """
    POST /uploads/from-url — body { uploadRequestId, sourceUrl }.

    The API fetches the bytes server-side (never through the model) and returns
    202 with the session in ``RECEIVING``. File contents never pass through this
    process. Poll ``get_upload_status`` until ``READY``.

    Errors are surfaced with the API's own message text so the caller can act on
    it: 400 for a disallowed or invalid source URL, 409 when the upload session
    is not in ``URL_ISSUED``.
    """
    payload = {
        "uploadRequestId": require_id(upload_request_id.strip(), "upload_request_id"),
        "sourceUrl": source_url.strip(),
    }
    async with _secure_client() as client:
        resp = await client.post(
            f"{base_url}/uploads/from-url",
            headers=_headers(api_key, caller_channel),
            json=payload,
            timeout=30.0,
        )
        _raise_for_status(resp, ids={"upload_request_id": upload_request_id})
        return resp.json()["data"]


async def get_upload_status(
    upload_request_id: str,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> dict:
    """
    GET /uploads/{uploadRequestId} — upload session state.

    ``data`` includes ``status`` (URL_ISSUED | RECEIVING | UPLOADED | VALIDATING |
    READY | REJECTED | CONSUMED | EXPIRED), ``nextStep``, ``filename``,
    ``projectId``, ``datasetId``, and when available ``sizeBytes``, ``rowCount``,
    ``error``, ``datasetVersionId``, ``sourceType``, plus timestamps.
    """
    url = f"{base_url}/uploads/{require_id(upload_request_id.strip(), 'upload_request_id')}"
    async with _secure_client() as client:
        resp = await client.get(
            url,
            headers=_headers(api_key, caller_channel),
            timeout=15.0,
        )
        _raise_for_status(resp)
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
        _raise_for_status(resp)


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
        _raise_for_status(resp, ids={
            "model_version_id": body.get("modelVersionId"),
            "dataset_version_id": body.get("datasetVersionId"),
        })
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
    url = f"{base_url}/training-jobs/{require_id(job_id, 'job_id')}"
    async with _secure_client() as client:
        resp = await client.get(
            url,
            headers=_headers(api_key, caller_channel),
            timeout=10.0,
        )
        _raise_for_status(resp)
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
        _raise_for_status(resp, ids={
            "model_version_id": body.get("modelVersionId"),
            "dataset_version_id": body.get("datasetVersionId"),
            "project_id": body.get("projectId"),
        })
        return resp.json()["data"]


async def get_prediction(
    prediction_id: str,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> dict:
    """GET /predictions/{predictionId}"""
    url = f"{base_url}/predictions/{require_id(prediction_id, 'prediction_id')}"
    async with _secure_client() as client:
        resp = await client.get(
            url,
            headers=_headers(api_key, caller_channel),
            timeout=15.0,
        )
        _raise_for_status(resp)
        return resp.json()["data"]


async def get_prediction_download(
    prediction_id: str,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> dict:
    """GET /predictions/{predictionId}/download"""
    url = f"{base_url}/predictions/{require_id(prediction_id, 'prediction_id')}/download"
    async with _secure_client() as client:
        resp = await client.get(
            url,
            headers=_headers(api_key, caller_channel),
            timeout=20.0,
        )
        _raise_for_status(resp)
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
        _raise_for_status(resp, ids={"project_id": project_id})
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
    url = f"{base_url}/projects/{require_id(project_id, 'project_id')}/models"
    async with _secure_client() as client:
        resp = await client.get(
            url,
            headers=_headers(api_key, caller_channel),
            timeout=10.0,
        )
        _raise_for_status(resp)
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
    url = (
        f"{base_url}/projects/{require_id(project_id, 'project_id')}"
        f"/models/{require_id(model_id, 'model_id')}/versions"
    )
    async with _secure_client() as client:
        resp = await client.get(
            url,
            headers=_headers(api_key, caller_channel),
            timeout=15.0,
        )
        _raise_for_status(resp)
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
    url = (
        f"{base_url}/projects/{require_id(project_id, 'project_id')}"
        f"/models/{require_id(model_id, 'model_id')}"
    )
    async with _secure_client() as client:
        resp = await client.get(
            url,
            headers=_headers(api_key, caller_channel),
            timeout=15.0,
        )
        _raise_for_status(resp)
        return resp.json()["data"]


async def get_model_by_id(
    model_id: str,
    *,
    api_key: str,
    base_url: str,
    caller_channel: str = "",
) -> dict:
    """GET /models/{modelId} — same payload as nested GET; project resolved server-side."""
    url = f"{base_url}/models/{require_id(model_id, 'model_id')}"
    async with _secure_client() as client:
        resp = await client.get(
            url,
            headers=_headers(api_key, caller_channel),
            timeout=15.0,
        )
        _raise_for_status(resp)
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
    url = (
        f"{base_url}/projects/{require_id(project_id, 'project_id')}"
        f"/models/{require_id(model_id, 'model_id')}"
    )
    async with _secure_client() as client:
        resp = await client.patch(
            url,
            headers=_headers(api_key, caller_channel),
            json=body,
            timeout=15.0,
        )
        _raise_for_status(resp)
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
    url = (
        f"{base_url}/projects/{require_id(project_id, 'project_id')}"
        f"/models/{require_id(model_id, 'model_id')}"
        f"/versions/{require_id(version_id, 'version_id')}"
    )
    async with _secure_client() as client:
        resp = await client.get(
            url,
            headers=_headers(api_key, caller_channel),
            timeout=15.0,
        )
        _raise_for_status(resp)
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
    url = f"{base_url}/projects/{require_id(project_id, 'project_id')}/models"
    async with _secure_client() as client:
        resp = await client.post(
            url,
            headers=_headers(api_key, caller_channel),
            json=body,
            timeout=15.0,
        )
        _raise_for_status(resp)
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
    body: { datasetVersionId, targetFeature, timeSeriesMode?, timeColumn? }
    """
    url = (
        f"{base_url}/projects/{require_id(project_id, 'project_id')}"
        f"/models/{require_id(model_id, 'model_id')}/versions"
    )
    async with _secure_client() as client:
        resp = await client.post(
            url,
            headers=_headers(api_key, caller_channel),
            json=body,
            timeout=30.0,
        )
        _raise_for_status(resp, ids={"dataset_version_id": body.get("datasetVersionId")})
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
    The returned dict may include ``_resolvedModelVersionId`` and ``_reportScope`` from API meta,
    plus the structured ``metrics`` object (or ``metricsUnavailableReason``) unchanged.
    """
    scope = report_scope.strip().lower() if report_scope else "summary"
    if scope not in ("summary", "full"):
        scope = "summary"
    params: dict[str, str] = {"scope": scope}
    if model_version_id.strip():
        params["versionId"] = model_version_id.strip()
    mid = require_id(model_id, "model_id")
    path = (
        f"{base_url}/projects/{require_id(project_id.strip(), 'project_id')}/models/{mid}/report"
        if project_id.strip()
        else f"{base_url}/models/{mid}/report"
    )
    async with _secure_client() as client:
        resp = await client.get(
            path,
            headers=_headers(api_key, caller_channel),
            params=params,
            timeout=60.0,
        )
        _raise_for_status(resp, ids={"model_version_id": model_version_id})
        payload = resp.json()
        data = payload.get("data")
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        if isinstance(data, dict):
            out = dict(data)
            if meta.get("modelVersionId") is not None:
                out["_resolvedModelVersionId"] = str(meta["modelVersionId"])
            if meta.get("reportScope") is not None:
                out["_reportScope"] = str(meta["reportScope"])
            # ``metrics`` / ``metricsUnavailableReason`` sit next to the report in
            # ``data``; accept them from ``meta`` too, and never reshape them.
            for key in ("metrics", "metricsUnavailableReason"):
                if key not in out and key in meta:
                    out[key] = meta[key]
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
    url = (
        f"{base_url}/projects/{require_id(project_id, 'project_id')}"
        f"/models/{require_id(model_id, 'model_id')}/endpoints"
    )
    async with _secure_client() as client:
        resp = await client.post(
            url,
            headers=_headers(api_key, caller_channel),
            json={},
            timeout=30.0,
        )
        _raise_for_status(resp)
        return resp.json()["data"]
