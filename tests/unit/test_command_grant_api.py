from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_execution_readiness import Backend, install
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime import execution_readiness as module
from xaa.runtime.command_contracts import CommandGrant
from xaa.runtime.execution_readiness import ExecutionReadiness, ReadinessRequest
from xaa.storage.runs import RunStore


@pytest.mark.parametrize(
    "fault", ["missing", "failed", "expired", "wall_expired", "profile", "platform", "checks"]
)
async def test_grant_needs_current_server_observation(workspace_tmp_path, monkeypatch, fault):
    install(monkeypatch, Backend())
    service = ExecutionReadiness()
    grant = CommandGrant(distribution="Ubuntu", profile_sha256="a" * 64)
    result = await service.check(ReadinessRequest(distribution="Ubuntu"))
    service.require_grant(grant)
    cache_key = ("wsl-bubblewrap-v1", "Ubuntu")
    if fault == "missing":
        service.cache.clear()
    elif fault == "expired":
        service.cache[cache_key] = (0, result)
    elif fault == "wall_expired":
        service.cache[cache_key] = (
            service.cache[cache_key][0],
            result.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}),
        )
    elif fault == "profile":
        monkeypatch.setattr(module, "profile_fingerprint", lambda: ("b" * 64, ()))
    elif fault == "platform":
        monkeypatch.setattr(module, "SUPPORTED", False)
    else:
        service.cache[cache_key] = (
            service.cache[cache_key][0],
            result.model_copy(
                update={"state": "unavailable"} if fault == "failed" else {"checks": []}
            ),
        )
    with pytest.raises(PermissionError):
        service.require_grant(grant)


def test_api_grants_only_authenticated_checked_workspace_runs(workspace_tmp_path, monkeypatch):
    backend = Backend()
    install(monkeypatch, backend)
    project = workspace_tmp_path / "project"
    project.mkdir()
    store = RunStore(workspace_tmp_path / "data" / "db.sqlite3")
    token = "grant-test-token-with-at-least-32-characters"
    headers = {"Authorization": "Bearer " + token}
    grant = {
        "backend": "wsl-bubblewrap-v1",
        "distribution": "Ubuntu",
        "profile_sha256": "a" * 64,
        "max_seconds": 30,
    }
    payload = {
        "objective": "Check project",
        "workspace": str(project),
        "mode": "workspace",
        "command_grant": grant,
    }
    with TestClient(create_app(LMStudioProvider(), SecretStr(token), store=store)) as client:
        assert client.post("/runs", json=payload).status_code == 401
        assert client.post("/runs", headers=headers, json=payload).status_code == 403
        assert backend.launches == 0
        assert (
            client.post(
                "/execution/readiness", headers=headers, json={"distribution": "Ubuntu"}
            ).json()["state"]
            == "ready"
        )
        for change, code in [
            ({"mode": "observe"}, 403),
            ({"workspace": str(store.path.parent)}, 403),
            ({"workspace": str(project / "missing")}, 409),
            ({"command_grant": {**grant, "profile_sha256": "b" * 64}}, 403),
            ({"command_grant": {**grant, "network": "allowed"}}, 422),
        ]:
            assert (
                client.post("/runs", headers=headers, json={**payload, **change}).status_code
                == code
            )
        assert client.get("/runs", headers=headers).json()["items"] == []
        response = client.post("/runs", headers=headers, json=payload)
        assert response.status_code == 201
        saved = response.json()
        assert saved["command_grant"] == {
            **grant,
            "image_reference": None,
            "docker_network_access": False,
            "docker_session_reuse": False,
            "docker_session_export": False,
            "ssh_identity_file": None,
        }
        assert client.get("/runs/" + saved["id"], headers=headers).json()["command_grant"] == {
            **grant,
            "image_reference": None,
            "docker_network_access": False,
            "docker_session_reuse": False,
            "docker_session_export": False,
            "ssh_identity_file": None,
        }
        events = store.list_events(saved["id"])
        record = next(
            event["payload"] for event in events if event["event_type"] == "execution.granted"
        )
        assert record["actor"] == "local_user" and record["workspace"] == str(project.resolve())
        ordinary = client.post("/runs", headers=headers, json={"objective": "Read only"}).json()
        assert ordinary["command_grant"] is None
    assert backend.launches == 1


def test_api_persists_only_server_checked_docker_image_grant(workspace_tmp_path, monkeypatch):
    image = "registry.example/xaa/python@sha256:" + "b" * 64

    class DockerBackend:
        profile_sha256 = "d" * 64

        async def verify_profile(self, *, deadline):
            return None

    backend = DockerBackend()
    discovered_network_permissions = []

    async def discover(authority, *, deadline):
        assert authority.image_reference == image
        discovered_network_permissions.append(authority.docker_network_access)
        return backend

    monkeypatch.setattr(module, "SUPPORTED", True)
    monkeypatch.setattr(module.DockerLocalTransport, "discover", discover)
    project = workspace_tmp_path / "docker-project"
    project.mkdir()
    store = RunStore(workspace_tmp_path / "docker-data" / "db.sqlite3")
    token = "docker-grant-test-token-with-at-least-32-characters"
    headers = {"Authorization": "Bearer " + token}
    with TestClient(create_app(LMStudioProvider(), SecretStr(token), store=store)) as client:
        readiness = client.post(
            "/execution/readiness",
            headers=headers,
            json={
                "backend": "docker-local-v1",
                "distribution": "Docker-local",
                "image_reference": image,
            },
        )
        assert readiness.status_code == 200
        assert readiness.json()["state"] == "ready"
        assert readiness.json()["image_reference"] == image
        grant = {
            "backend": "docker-local-v1",
            "distribution": "Docker-local",
            "profile_sha256": backend.profile_sha256,
            "max_seconds": 30,
            "image_reference": image,
            "docker_network_access": True,
        }
        response = client.post(
            "/runs",
            headers=headers,
            json={
                "objective": "Check project in Docker",
                "workspace": str(project),
                "mode": "workspace",
                "command_grant": grant,
            },
        )
        assert response.status_code == 201
        run = response.json()
        assert run["command_grant"] == {
            **grant,
            "docker_session_reuse": False,
            "docker_session_export": False,
            "ssh_identity_file": None,
        }
        assert discovered_network_permissions and not any(discovered_network_permissions)
        event = next(
            item["payload"]
            for item in store.list_events(run["id"])
            if item["event_type"] == "execution.granted"
        )
        assert event["grant"]["image_reference"] == image
        assert event["grant"]["docker_network_access"] is True
        stale = {**grant, "profile_sha256": "e" * 64}
        assert (
            client.post(
                "/runs",
                headers=headers,
                json={
                    "objective": "Stale grant",
                    "workspace": str(project),
                    "mode": "workspace",
                    "command_grant": stale,
                },
            ).status_code
            == 403
        )
