from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider, ModelInfo
from xaa.providers.openai_compatible import OpenAICompatibleProvider
from xaa.providers.router import OPENAI_MODEL_PREFIX, ProviderRouter
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


class FixtureCompatibleProvider(OpenAICompatibleProvider):
    def __init__(self) -> None:
        super().__init__("https://compatible.example.test")

    async def models(self) -> list[ModelInfo]:
        return [ModelInfo(id="remote/model")]

    async def health(self) -> bool:
        return True


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


def test_compatible_provider_settings_and_token_use_named_vault_profile(workspace_tmp_path):
    class ProfileCredentials:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        def get(self, profile: str = "lm-studio") -> str | None:
            return self.values.get(profile)

        def set(self, token: str, profile: str = "lm-studio") -> None:
            self.values[profile] = token

        def delete(self, profile: str = "lm-studio") -> None:
            self.values.pop(profile, None)

    credentials = ProfileCredentials()
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(
        create_app(
            AvailableProvider(),
            SecretStr(TOKEN),
            store=RunStore(workspace_tmp_path / "compatible-provider.sqlite3"),
            credential_store=credentials,
        )
    ) as client:
        assert client.get("/providers/openai-compatible/config", headers=headers).json() == {
            "endpoint": None,
            "selected_model": None,
        }
        assert (
            client.put(
                "/providers/openai-compatible/config",
                headers=headers,
                json={"endpoint": "http://203.0.113.8"},
            ).status_code
            == 422
        )
        configured = client.put(
            "/providers/openai-compatible/config",
            headers=headers,
            json={"endpoint": "https://api.example.test"},
        )
        assert configured.status_code == 200
        assert configured.json() == {
            "endpoint": "https://api.example.test",
            "selected_model": None,
        }
        token = "compatible-provider-secret-value"
        saved = client.put(
            "/providers/openai-compatible/credential",
            headers=headers,
            json={"token": token},
        )
        assert saved.status_code == 200
        assert saved.json() == {"configured": True}
        assert token not in saved.text
        assert credentials.get("openai-compatible") == token
        assert client.get("/providers/openai-compatible/credential", headers=headers).json() == {
            "configured": True
        }
        cleared = client.delete("/providers/openai-compatible/credential", headers=headers)
        assert cleared.json() == {"configured": False}
        assert credentials.get("openai-compatible") is None


def test_model_selection_is_exclusive_and_run_id_keeps_adapter(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "provider-selection.sqlite3")
    router = ProviderRouter(AvailableProvider(), FixtureCompatibleProvider())
    with TestClient(create_app(router, SecretStr(TOKEN), store=store)) as client:
        headers = {"Authorization": f"Bearer {TOKEN}"}
        external = f"{OPENAI_MODEL_PREFIX}remote/model"
        selected = client.put(
            "/providers/openai-compatible/selection",
            headers=headers,
            json={"model": external},
        )
        assert selected.status_code == 200
        with store.connection() as connection:
            assert (
                connection.execute(
                    "SELECT selected_model FROM provider_profiles WHERE id='openai-compatible'"
                ).fetchone()[0]
                == external
            )
            assert (
                connection.execute(
                    "SELECT selected_model FROM provider_profiles WHERE id='lm-studio'"
                ).fetchone()[0]
                is None
            )
        local = client.put(
            "/providers/lm-studio/selection",
            headers=headers,
            json={"model": "fixture/model"},
        )
        assert local.status_code == 200
        with store.connection() as connection:
            assert (
                connection.execute(
                    "SELECT selected_model FROM provider_profiles WHERE id='openai-compatible'"
                ).fetchone()[0]
                is None
            )
            assert (
                connection.execute(
                    "SELECT selected_model FROM provider_profiles WHERE id='lm-studio'"
                ).fetchone()[0]
                == "fixture/model"
            )
        run = store.create_run(
            objective="Keep provider identity",
            acceptance_criteria=[],
            workspace=None,
            mode="observe",
            model=external,
        )
        assert run["model"] == external


def test_brave_search_credential_uses_named_os_vault_profile(workspace_tmp_path):
    class ProfileCredentials:
        def __init__(self):
            self.values: dict[str, str] = {}

        def get(self, profile="lm-studio"):
            return self.values.get(profile)

        def set(self, token, profile="lm-studio"):
            self.values[profile] = token

        def delete(self, profile="lm-studio"):
            self.values.pop(profile, None)

    credentials = ProfileCredentials()
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(
        create_app(
            AvailableProvider(),
            SecretStr(TOKEN),
            store=RunStore(workspace_tmp_path / "brave-search-credential.sqlite3"),
            credential_store=credentials,
        )
    ) as client:
        assert client.get("/providers/brave-search/credential", headers=headers).json() == {
            "configured": False
        }
        secret = "brave-search-secret-test-value"
        saved = client.put(
            "/providers/brave-search/credential", headers=headers, json={"token": secret}
        )
        assert saved.status_code == 200
        assert secret not in saved.text
        assert credentials.get("brave-search") == secret
        assert client.get("/providers/brave-search/credential", headers=headers).json() == {
            "configured": True
        }
        assert client.delete("/providers/brave-search/credential", headers=headers).json() == {
            "configured": False
        }
