from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider, ModelInfo
from xaa.storage.runs import RunStore

TOKEN = "provider-settings-test-token-long-enough-for-validation"


class AvailableProvider(LMStudioProvider):
    async def health(self) -> bool:
        return True

    async def models(self) -> list[ModelInfo]:
        return [ModelInfo(id="fixture/model")]

    async def stream(self, *, model: str, messages: list[dict[str, Any]]) -> AsyncIterator[str]:
        yield "{}"

    async def close(self) -> None:
        return None


class MemoryCredentialStore:
    def __init__(self) -> None:
        self.value: str | None = None

    def get(self, profile: str = "lm-studio") -> str | None:
        return self.value

    def set(self, token: str, profile: str = "lm-studio") -> None:
        self.value = token

    def delete(self, profile: str = "lm-studio") -> None:
        self.value = None


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://127.0.0.1:1234",
        "http://localhost:1234/",
        "http://[::1]:1234",
        "http://127.0.0.2:9999",
    ],
)
def test_lm_studio_endpoint_accepts_loopback_addresses(endpoint: str) -> None:
    assert LMStudioProvider.validate_endpoint(endpoint).startswith("http://")


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://127.0.0.1:1234",
        "http://8.8.8.8:1234",
        "http://user:password@127.0.0.1:1234",
        "http://127.0.0.1:1234/api",
        "http://127.0.0.1:1234/?redirect=1",
        "file:///tmp/model-server",
    ],
)
def test_lm_studio_endpoint_rejects_non_loopback_or_ambiguous_urls(endpoint: str) -> None:
    with pytest.raises(ValueError):
        LMStudioProvider.validate_endpoint(endpoint)


def test_authenticated_provider_settings_persist_endpoint_and_model(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "provider.sqlite3")
    provider = AvailableProvider()
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(provider, SecretStr(TOKEN), store=store)) as client:
        assert client.get("/providers/lm-studio/config").status_code == 401
        initial = client.get("/providers/lm-studio/config", headers=headers)
        assert initial.json() == {
            "endpoint": "http://127.0.0.1:1234",
            "selected_model": None,
        }
        refused = client.put(
            "/providers/lm-studio/config",
            headers=headers,
            json={"endpoint": "http://192.0.2.1:1234"},
        )
        assert refused.status_code == 422
        saved = client.put(
            "/providers/lm-studio/config",
            headers=headers,
            json={"endpoint": "http://localhost:8123"},
        )
        assert saved.status_code == 200
        assert saved.json() == {"endpoint": "http://127.0.0.1:8123", "selected_model": None}
        selected = client.put(
            "/providers/lm-studio/selection",
            headers=headers,
            json={"model": "fixture/model"},
        )
        assert selected.status_code == 200
        assert client.get("/providers/lm-studio/config", headers=headers).json() == {
            "endpoint": "http://127.0.0.1:8123",
            "selected_model": "fixture/model",
        }
        assert (
            client.put(
                "/providers/lm-studio/selection",
                headers=headers,
                json={"model": "missing/model"},
            ).status_code
            == 422
        )


def test_provider_endpoint_cannot_change_while_worker_owns_a_run(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "provider-active.sqlite3")
    run = store.create_run(
        objective="Active", acceptance_criteria=[], workspace=None, mode="observe", model=None
    )
    assert store.acquire_lease(run["id"], "worker")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(AvailableProvider(), SecretStr(TOKEN), store=store)) as client:
        response = client.put(
            "/providers/lm-studio/config",
            headers=headers,
            json={"endpoint": "http://localhost:8123"},
        )
    assert response.status_code == 409
    assert store.get_run(run["id"])["lease_owner"] is not None


def test_provider_configuration_put_allows_browser_preflight(workspace_tmp_path):
    with TestClient(
        create_app(
            AvailableProvider(),
            SecretStr(TOKEN),
            store=RunStore(workspace_tmp_path / "cors.sqlite3"),
        )
    ) as client:
        response = client.options(
            "/providers/lm-studio/config",
            headers={
                "Origin": "http://tauri.localhost",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
    assert response.status_code == 200
    assert "PUT" in response.headers["access-control-allow-methods"]


def test_lm_studio_credential_is_saved_and_never_returned(workspace_tmp_path):
    credentials = MemoryCredentialStore()
    provider = AvailableProvider()
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(
        create_app(
            provider,
            SecretStr(TOKEN),
            store=RunStore(workspace_tmp_path / "credential.sqlite3"),
            credential_store=credentials,
        )
    ) as client:
        assert client.get("/providers/lm-studio/credential", headers=headers).json() == {
            "configured": False
        }
        secret = "lm-studio-secret-test-value"
        saved = client.put(
            "/providers/lm-studio/credential", headers=headers, json={"token": secret}
        )
        assert saved.status_code == 200
        assert saved.json() == {"configured": True}
        assert secret not in saved.text
        assert credentials.get() == secret
        assert provider._credential is not None
        assert provider._credential.get_secret_value() == secret
        cleared = client.delete("/providers/lm-studio/credential", headers=headers)
        assert cleared.status_code == 200
        assert cleared.json() == {"configured": False}
        assert credentials.get() is None
        assert provider._credential is None
