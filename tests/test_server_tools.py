"""Tests for easydeploy_ai_mcp.server (EDA MCP tools) and api_client helpers."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastmcp import Client

import easydeploy_ai_mcp.server as _eda_mod

BASE = "https://api.example.com/v1"
API_KEY = "test-key"


@pytest.fixture()
def eda_mcp_server():
    with patch.object(_eda_mod, "_BASE_URL", BASE), patch.object(_eda_mod, "_API_KEY", API_KEY):
        yield _eda_mod.mcp


@pytest.mark.asyncio
async def test_eda_mcp_registered_tools_match_manifest(eda_mcp_server):
    async with Client(eda_mcp_server) as client:
        tools = await client.list_tools()
    names = {t.name for t in tools}
    assert names == _eda_mod.EDA_MCP_TOOL_NAMES


@pytest.mark.asyncio
async def test_eda_mcp_get_account_status_calls_correct_endpoint(eda_mcp_server):
    account_payload = {
        "tier": "starter",
        "credits_remaining": 3,
        "credits_per_cycle": 5,
        "predictions_remaining": 999_000,
        "predictions_limit": 1_000_000,
        "endpoints_active": 0,
        "endpoints_limit": 1,
    }
    mock_fn = AsyncMock(return_value=account_payload)
    with patch("easydeploy_ai_mcp.server.api_client.get_account_status", mock_fn):
        async with Client(eda_mcp_server) as client:
            result = await client.call_tool(
                "get_account_status", {"customer_id": "cust_123"}
            )

    assert not result.is_error
    assert result.data["tier"] == "starter"
    mock_fn.assert_called_once()
    _args, _kwargs = mock_fn.call_args
    assert _args[0] == "cust_123"
    assert _kwargs["base_url"] == BASE
    assert _kwargs["api_key"] == API_KEY


@pytest.mark.asyncio
async def test_eda_mcp_list_projects_delegates_to_api_client(eda_mcp_server):
    projects_payload = [
        {"id": "proj-1", "name": "Demo", "description": None, "createdAt": "2025-01-01T00:00:00Z"},
    ]
    mock_fn = AsyncMock(return_value=projects_payload)
    with patch("easydeploy_ai_mcp.server.api_client.list_projects", mock_fn):
        async with Client(eda_mcp_server) as client:
            result = await client.call_tool("list_projects", {})

    assert not result.is_error
    data = json.loads(result.content[0].text)
    assert data[0]["id"] == "proj-1"
    mock_fn.assert_called_once()
    _kwargs = mock_fn.call_args[1]
    assert _kwargs["base_url"] == BASE
    assert _kwargs["api_key"] == API_KEY


@pytest.mark.asyncio
async def test_eda_mcp_create_project_delegates_to_api_client(eda_mcp_server):
    payload = {"id": "proj-new", "name": "Alpha", "description": "desc", "createdAt": "2025-01-01T00:00:00Z"}
    mock_fn = AsyncMock(return_value=payload)
    with patch("easydeploy_ai_mcp.server.api_client.create_project", mock_fn):
        async with Client(eda_mcp_server) as client:
            result = await client.call_tool(
                "create_project", {"name": "Alpha", "description": "desc"}
            )

    assert not result.is_error
    assert result.data["id"] == "proj-new"
    mock_fn.assert_called_once()
    body = mock_fn.call_args[0][0]
    assert body["name"] == "Alpha"
    assert body["description"] == "desc"


@pytest.mark.asyncio
async def test_eda_mcp_start_upload_delegates_to_presign(eda_mcp_server):
    presign = {
        "gatewayUploadUrl": "https://api.example.com/prod/v1/uploads/data?uploadToken=tok",
        "uploadRequestId": "upreq-1",
        "datasetId": "ds-new",
    }
    mock_fn = AsyncMock(return_value=presign)
    with patch("easydeploy_ai_mcp.server.api_client.get_presigned_upload_url", mock_fn):
        async with Client(eda_mcp_server) as client:
            result = await client.call_tool("start_upload", {
                "filename": "train.csv",
                "project_id": "p1",
            })
    assert not result.is_error
    assert result.data["upload_request_id"] == "upreq-1"
    assert "gateway_curl_command" not in result.data
    assert "curl_command" in result.data
    mock_fn.assert_called_once()


@pytest.mark.asyncio
async def test_eda_mcp_complete_upload_delegates_to_client(eda_mcp_server):
    payload = {"dataset": {"id": "ds-1"}, "datasetVersion": {"id": "dv-1"}}
    mock_fn = AsyncMock(return_value=payload)
    with patch("easydeploy_ai_mcp.server.api_client.complete_dataset_upload", mock_fn):
        async with Client(eda_mcp_server) as client:
            result = await client.call_tool("complete_upload", {
                "project_id": "p1",
                "name": "Churn",
                "upload_request_id": "upreq-1",
                "dataset_id": "ds-1",
            })
    assert not result.is_error
    mock_fn.assert_called_once()
    _args, _kwargs = mock_fn.call_args
    assert _args[0] == "p1"
    assert _args[1]["uploadRequestId"] == "upreq-1"
    assert _args[1]["datasetId"] == "ds-1"


@pytest.mark.asyncio
async def test_eda_mcp_list_dataset_versions_delegates(eda_mcp_server):
    payload = [{"id": "v1", "version": 1, "datasetId": "ds1", "projectId": "p1"}]
    mock_fn = AsyncMock(return_value=payload)
    with patch("easydeploy_ai_mcp.server.api_client.list_dataset_versions", mock_fn):
        async with Client(eda_mcp_server) as client:
            result = await client.call_tool(
                "list_dataset_versions",
                {"dataset_id": "ds1", "project_id": "p1"},
            )

    assert not result.is_error
    data = json.loads(result.content[0].text)
    assert data[0]["id"] == "v1"
    mock_fn.assert_called_once_with("ds1", "p1", api_key=API_KEY, base_url=BASE, caller_channel="MCP_AGENT")


@pytest.mark.asyncio
async def test_eda_mcp_create_model_version_delegates(eda_mcp_server):
    payload = {"id": "mv-1", "modelId": "m1", "version": 1, "status": "DRAFT"}
    mock_fn = AsyncMock(return_value=payload)
    with patch("easydeploy_ai_mcp.server.api_client.create_model_version", mock_fn):
        async with Client(eda_mcp_server) as client:
            result = await client.call_tool(
                "create_model_version",
                {
                    "project_id": "p1",
                    "model_id": "m1",
                    "dataset_version_id": "dv1",
                    "target_feature": "label",
                },
            )

    assert not result.is_error
    mock_fn.assert_called_once()
    body = mock_fn.call_args[0][2]
    assert body["datasetVersionId"] == "dv1"
    assert body["targetFeature"] == "label"


@pytest.mark.asyncio
async def test_eda_mcp_submit_training_job_accepts_dataset_version_id(eda_mcp_server):
    job_payload = {"jobId": "job_abc", "modelVersionId": "mv_xyz", "status": "SUBMITTED"}
    mock_fn = AsyncMock(return_value=job_payload)
    with patch("easydeploy_ai_mcp.server.api_client.submit_training_job", mock_fn):
        async with Client(eda_mcp_server) as client:
            result = await client.call_tool("submit_training_job", {
                "model_version_id": "mv_xyz",
                "dataset_version_id": "ver_qa_001",
            })

    assert not result.is_error
    mock_fn.assert_called_once()
    body = mock_fn.call_args[0][0]
    assert body["modelVersionId"] == "mv_xyz"
    assert body["datasetVersionId"] == "ver_qa_001"
    assert "fileUrl" not in body


@pytest.mark.asyncio
async def test_eda_mcp_create_dataset_version_sets_type(eda_mcp_server):
    ver_payload = {"datasetVersion": {"id": "ver_clean_01"}, "id": "ver_clean_01"}
    mock_fn = AsyncMock(return_value=ver_payload)
    with patch("easydeploy_ai_mcp.server.api_client.create_dataset_version", mock_fn):
        async with Client(eda_mcp_server) as client:
            result = await client.call_tool("create_dataset_version", {
                "project_id": "proj_1",
                "dataset_id": "ds_1",
                "version_type": "qa_cleaned",
                "file_url": "s3://my-bucket/path/clean.csv",
                "qa_metadata": {"source_version_id": "ver_raw_01"},
            })

    assert not result.is_error
    mock_fn.assert_called_once()
    _args, _kwargs = mock_fn.call_args
    assert _args[0] == "proj_1"
    assert _args[1] == "ds_1"
    body = _args[2]
    assert body["version_type"] == "qa_cleaned"
    assert _kwargs["base_url"] == BASE


@pytest.mark.asyncio
async def test_eda_mcp_get_prediction_sanitizes_response(eda_mcp_server):
    payload = {
        "id": "pred_1",
        "status": "COMPLETED",
        "type": "ADHOC",
        "projectId": "proj_1",
        "modelVersionId": "mv_1",
        "output": {"prediction": "yes", "probability": 0.91},
        "outputDataPath": "s3://secret-bucket/path.csv",
        "adhocInput": '{"big": "input"}',
        "adhocOutput": '{"internal": true}',
        "owner": "user_123",
        "ownerId": "user_123",
        "createdBy": "user_123",
    }
    mock_fn = AsyncMock(return_value=payload)
    with patch("easydeploy_ai_mcp.server.api_client.get_prediction", mock_fn):
        async with Client(eda_mcp_server) as client:
            result = await client.call_tool("get_prediction", {"prediction_id": "pred_1"})

    assert not result.is_error
    data = result.data
    assert data["id"] == "pred_1"
    assert data["output"]["probability"] == 0.91
    assert "outputDataPath" not in data
    assert "adhocInput" not in data
    assert "adhocOutput" not in data
    assert "owner" not in data
    assert "ownerId" not in data
    assert "createdBy" not in data
    mock_fn.assert_called_once()


@pytest.mark.asyncio
async def test_eda_mcp_get_prediction_batch_flags_output_available(eda_mcp_server):
    payload = {
        "id": "pred_b1",
        "status": "COMPLETED",
        "type": "BATCH",
        "outputDataPath": "s3://bucket/out.csv",
    }
    mock_fn = AsyncMock(return_value=payload)
    with patch("easydeploy_ai_mcp.server.api_client.get_prediction", mock_fn):
        async with Client(eda_mcp_server) as client:
            result = await client.call_tool("get_prediction", {"prediction_id": "pred_b1"})

    assert not result.is_error
    assert result.data["batch_output_available"] is True
    assert "outputDataPath" not in result.data


@pytest.mark.asyncio
async def test_eda_mcp_list_predictions_sanitizes_responses(eda_mcp_server):
    payload = [
        {"id": "pred_1", "status": "PENDING", "inputDataPath": "s3://bucket/in.csv", "owner": "u1"},
    ]
    mock_fn = AsyncMock(return_value=payload)
    with patch("easydeploy_ai_mcp.server.api_client.list_predictions", mock_fn):
        async with Client(eda_mcp_server) as client:
            result = await client.call_tool("list_predictions", {"project_id": "proj_1"})

    assert not result.is_error
    data = json.loads(result.content[0].text)
    assert data[0]["id"] == "pred_1"
    assert "inputDataPath" not in data[0]
    assert "owner" not in data[0]


@pytest.mark.asyncio
async def test_eda_mcp_get_batch_prediction_download_url_returns_url(eda_mcp_server):
    download_meta = {
        "gatewayDownloadUrl": "https://api.example.com/v1/predictions/download?downloadToken=tok",
        "expiresInSeconds": 900,
    }
    mock_meta = AsyncMock(return_value=download_meta)
    mock_get = AsyncMock(return_value={"id": "pred_1", "status": "COMPLETED", "downloadReady": True})
    with patch("easydeploy_ai_mcp.server.api_client.get_prediction", mock_get), \
         patch("easydeploy_ai_mcp.server.api_client.get_prediction_download", mock_meta):
        async with Client(eda_mcp_server) as client:
            result = await client.call_tool("get_prediction", {
                "prediction_id": "pred_1",
            })

    assert not result.is_error
    assert result.data["download_url"] == "https://api.example.com/v1/predictions/download"
    assert "curl_command" in result.data
    assert "X-Download-Token: tok" in result.data["curl_command"]
    assert "downloadToken=tok" not in result.data["curl_command"]
    mock_meta.assert_called_once_with("pred_1", api_key=API_KEY, base_url=BASE, caller_channel="MCP_AGENT")


@pytest.mark.asyncio
async def test_eda_mcp_run_prediction_waits_and_returns_sanitized_result(eda_mcp_server):
    submit_mock = AsyncMock(return_value={"id": "pred_1", "status": "PENDING"})
    get_mock = AsyncMock(side_effect=[
        {"id": "pred_1", "status": "PENDING"},
        {
            "id": "pred_1", "status": "COMPLETED",
            "output": {"label": "churned", "probability": 0.82},
            "outputDataPath": "s3://bucket/out.csv",
            "owner": "u1",
        },
    ])

    with patch("easydeploy_ai_mcp.server.api_client.run_prediction", submit_mock), \
         patch("easydeploy_ai_mcp.server.api_client.get_prediction", get_mock):
        async with Client(eda_mcp_server) as client:
            result = await client.call_tool("run_prediction", {
                "project_id": "proj_1",
                "model_version_id": "mv_1",
                "target_feature": "churned",
                "input_data": {"arr": 1000},
                "wait_for_result": True,
                "poll_interval_seconds": 0.01,
                "max_wait_seconds": 2,
            })

    assert not result.is_error
    assert result.data["id"] == "pred_1"
    assert result.data["status"] == "COMPLETED"
    assert result.data["output"]["label"] == "churned"
    assert "outputDataPath" not in result.data
    assert "owner" not in result.data
    assert submit_mock.call_count == 1


@pytest.mark.asyncio
async def test_eda_mcp_run_prediction_no_wait_returns_prediction_id(eda_mcp_server):
    submit_mock = AsyncMock(return_value={"id": "pred_1", "status": "PENDING"})
    with patch("easydeploy_ai_mcp.server.api_client.run_prediction", submit_mock):
        async with Client(eda_mcp_server) as client:
            result = await client.call_tool("run_prediction", {
                "project_id": "proj_1",
                "model_version_id": "mv_1",
                "target_feature": "churned",
                "input_data": {"arr": 1000},
                "wait_for_result": False,
            })

    assert not result.is_error
    assert result.data["prediction_id"] == "pred_1"
    assert result.data["status"] == "PENDING"


@pytest.mark.asyncio
async def test_eda_mcp_run_batch_prediction_no_s3_params_needed(eda_mcp_server):
    submit_mock = AsyncMock(return_value={"id": "pred_batch_1", "status": "PENDING"})
    with patch("easydeploy_ai_mcp.server.api_client.run_prediction", submit_mock):
        async with Client(eda_mcp_server) as client:
            result = await client.call_tool("run_batch_prediction", {
                "project_id": "proj_1",
                "model_version_id": "mv_1",
                "target_feature": "churned",
                "dataset_version_id": "dsv_1",
                "wait_for_result": False,
            })

    assert not result.is_error
    assert result.data["prediction_id"] == "pred_batch_1"
    assert result.data["status"] == "PENDING"
    submit_mock.assert_called_once()
    body = submit_mock.call_args[0][0]
    assert body["projectId"] == "proj_1"
    assert body["datasetVersionId"] == "dsv_1"
    assert "inputDataPath" not in body
    assert "outputDataPath" not in body


@pytest.mark.asyncio
async def test_eda_mcp_run_batch_prediction_waits_and_sanitizes(eda_mcp_server):
    submit_mock = AsyncMock(return_value={"id": "pred_batch_1", "status": "PENDING"})
    get_mock = AsyncMock(side_effect=[
        {"id": "pred_batch_1", "status": "PENDING"},
        {
            "id": "pred_batch_1", "status": "COMPLETED",
            "type": "BATCH",
            "outputDataPath": "s3://bucket/out.csv",
            "owner": "u1",
        },
    ])
    with patch("easydeploy_ai_mcp.server.api_client.run_prediction", submit_mock), \
         patch("easydeploy_ai_mcp.server.api_client.get_prediction", get_mock):
        async with Client(eda_mcp_server) as client:
            result = await client.call_tool("run_batch_prediction", {
                "project_id": "proj_1",
                "model_version_id": "mv_1",
                "target_feature": "churned",
                "dataset_version_id": "dsv_1",
                "wait_for_result": True,
                "poll_interval_seconds": 0.01,
                "max_wait_seconds": 2,
            })

    assert not result.is_error
    assert result.data["id"] == "pred_batch_1"
    assert result.data["status"] == "COMPLETED"
    assert result.data["batch_output_available"] is True
    assert "outputDataPath" not in result.data
    assert "owner" not in result.data


@pytest.mark.asyncio
async def test_eda_mcp_patch_dataset_version_updates_qa_status(eda_mcp_server):
    ver_payload = {"id": "ver_raw_01", "qa_status": "ready", "version_type": "raw"}
    mock_fn = AsyncMock(return_value=ver_payload)
    with patch("easydeploy_ai_mcp.server.api_client.patch_dataset_version", mock_fn):
        async with Client(eda_mcp_server) as client:
            result = await client.call_tool("create_dataset_version", {
                "project_id": "proj_1",
                "dataset_id": "ds_1",
                "version_id": "ver_raw_01",
                "qa_status": "ready",
            })

    assert not result.is_error
    mock_fn.assert_called_once()
    _args, _kwargs = mock_fn.call_args
    assert _args[0] == "proj_1"
    assert _args[1] == "ds_1"
    assert _args[2] == "ver_raw_01"
    assert _args[3] == {"qa_status": "ready"}
    assert _kwargs["base_url"] == BASE


@pytest.mark.asyncio
async def test_eda_mcp_start_upload_returns_gateway_curl_command(eda_mcp_server):
    presign = {
        "gatewayUploadUrl": "https://api.example.com/prod/v1/uploads/data?uploadToken=tok",
        "uploadRequestId": "upreq-1",
        "datasetId": "ds-new",
    }
    mock_fn = AsyncMock(return_value=presign)
    with patch("easydeploy_ai_mcp.server.api_client.get_presigned_upload_url", mock_fn):
        async with Client(eda_mcp_server) as client:
            result = await client.call_tool("start_upload", {
                "filename": "data.csv",
                "project_id": "p1",
            })
    assert not result.is_error
    data = result.data

    assert "curl_command" in data
    assert "Authorization" not in data["curl_command"]
    assert "X-S3-Key" not in data["curl_command"]
    assert "FILE_PATH" in data["curl_command"]
    assert "X-Upload-Token: tok" in data["curl_command"]
    assert "uploadToken=tok" not in data["curl_command"]

    assert data["upload_request_id"] == "upreq-1"
    assert "s3Key" not in data
    assert "bucket" not in data
    assert "fileUrl" not in data
    assert "gatewayUploadUrl" not in data
    assert "uploadUrl" not in data

    assert "next_steps" in data
    assert "complete_upload" in data["next_steps"]


@pytest.mark.asyncio
async def test_api_client_rejects_http_presigned_url():
    from easydeploy_ai_mcp import api_client

    with pytest.raises(ValueError, match="non-HTTPS"):
        await api_client.upload_to_s3(
            "http://insecure.example.com/upload",
            b"data",
        )


@pytest.mark.asyncio
async def test_api_client_accepts_https_presigned_url():
    from easydeploy_ai_mcp import api_client

    mock_resp = AsyncMock()
    mock_resp.raise_for_status = lambda: None
    mock_client = AsyncMock()
    mock_client.put = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    with patch("easydeploy_ai_mcp.api_client._secure_client", return_value=mock_client):
        await api_client.upload_to_s3(
            "https://api.example.com/v1/uploads/data",
            b"data",
        )
    mock_client.put.assert_called_once()
    _kwargs = mock_client.put.call_args[1]
    assert _kwargs["headers"]["Content-Type"] == "text/csv"
