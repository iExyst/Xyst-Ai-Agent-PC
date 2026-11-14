from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider
from xaa.storage.runs import RunStore

TOKEN = "system-resource-api-test-token-with-more-than-32-characters"


def test_system_resource_endpoint_requires_local_api_authentication(
    workspace_tmp_path: Path,
) -> None:
    provider = LMStudioProvider()
    app = create_app(
        provider,
        SecretStr(TOKEN),
        store=RunStore(workspace_tmp_path / "system-resources.sqlite3"),
    )
    with TestClient(app) as client:
        assert client.get("/system/resources").status_code == 401
        response = client.get("/system/resources", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["logical_processors"] >= 1
    assert snapshot["queue_active"] is False
    assert snapshot["queue_waiting"] == 0
    assert snapshot["queue_reserved_tokens"] == 0
    assert snapshot["queue_max_reserved_tokens"] == 2_000_000
    assert snapshot["accelerator_telemetry_available"] is bool(snapshot["gpu_devices"])
    assert all(
        device["memory_free_bytes"] is None or device["memory_free_bytes"] >= 0
        for device in snapshot["gpu_devices"]
    )
