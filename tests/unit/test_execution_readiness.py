import asyncio
import base64
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime import execution_readiness as module
from xaa.runtime.execution_readiness import ExecutionReadiness, ReadinessRequest
from xaa.storage.runs import RunStore


class Backend:
    profile_sha256 = "a" * 64

    def __init__(self, fault=None):
        self.fault = fault
        self.launches = 0
        self.controls = []

    async def run(self, intent, snapshot, *, deadline):
        self.launches += 1
        assert intent.argv == ["/usr/bin/python3", "-I", "-c", module.PROBE]
        assert len(snapshot.files) == 1 and snapshot.files[0].path == "probe.txt"
        nonce = snapshot.files[0].content.decode()
        if self.fault == "run":
            raise OSError("do not expose raw fixture backend error")
        output = {
            "nonce": "wrong" if self.fault == "nonce" else nonce,
            "checks": 1 if self.fault == "numeric_bool" else True,
        }
        return {
            "backend": "wsl-bubblewrap-v1",
            "output_complete": True,
            "workspace_changes_persisted": False,
            "limits": {
                "max_active_executions": 2,
                "memory_bytes": 1 if self.fault == "limits" else 134217728,
                "cpu_percent": 50,
                "tasks": 32,
                "scratch_bytes": 33554432,
                "workspace_bytes": 33554432,
                "output_bytes": 1048576,
                "seconds": 5,
                "network": "none",
            },
            "succeeded": True,
            "cleanup_confirmed": True,
            "exit_code": 0,
            "snapshot_sha256": snapshot.sha256,
            "stdout_b64": base64.b64encode(json.dumps(output).encode()).decode(),
        }

    async def control(self, action, intent, *, deadline):
        self.controls.append(action)
        return {
            "cleanup_confirmed": self.fault != "cleanup",
            "closed": True,
            "manager_alive": False,
        }


def install(monkeypatch, backend):
    monkeypatch.setattr(module, "SUPPORTED", True)
    monkeypatch.setattr(module, "profile_fingerprint", lambda: ("a" * 64, ()))

    async def discover(distribution, *, deadline):
        return backend

    monkeypatch.setattr(module.WSLTransport, "discover", discover)


@pytest.mark.parametrize("fault", [None, "run", "nonce", "numeric_bool", "cleanup", "limits"])
async def test_probe_requires_fixed_output_and_observed_cleanup(monkeypatch, fault):
    backend = Backend(fault)
    install(monkeypatch, backend)
    service = ExecutionReadiness()
    request = ReadinessRequest(distribution="Ubuntu")
    result = await service.check(request)
    again = await service.check(request)
    assert result.state == ("ready" if fault is None else "unavailable")
    assert result.checks == (list(module.CHECKS) if fault is None else [])
    assert again.checked_at == result.checked_at and backend.launches == 1
    assert "cleanup" in backend.controls
    assert "raw fixture" not in result.model_dump_json()


async def test_busy_probe_does_not_queue_or_spawn(monkeypatch):
    backend = Backend()
    install(monkeypatch, backend)
    service = ExecutionReadiness()
    await service.lock.acquire()
    try:
        result = await service.check(ReadinessRequest(distribution="Ubuntu"))
    finally:
        service.lock.release()
    assert result.state == "busy" and backend.launches == 0


async def test_profile_change_invalidates_cached_success(monkeypatch):
    backend = Backend()
    install(monkeypatch, backend)
    service = ExecutionReadiness()
    request = ReadinessRequest(distribution="Ubuntu")
    assert (await service.check(request)).state == "ready"
    monkeypatch.setattr(module, "profile_fingerprint", lambda: ("b" * 64, ()))
    assert (await service.check(request)).state == "unavailable"
    assert backend.launches == 1


async def test_unsupported_platform_does_not_spawn(monkeypatch):
    backend = Backend()
    install(monkeypatch, backend)
    monkeypatch.setattr(module, "SUPPORTED", False)
    assert (
        await ExecutionReadiness().check(ReadinessRequest(distribution="Ubuntu"))
    ).state == "unsupported"
    assert backend.launches == 0


async def test_cancellation_waits_for_cleanup_and_does_not_cache_ready(monkeypatch):
    started = asyncio.Event()
    cleaning = asyncio.Event()
    release = asyncio.Event()

    class WaitingBackend(Backend):
        async def run(self, intent, snapshot, *, deadline):
            started.set()
            await asyncio.Event().wait()

        async def control(self, action, intent, *, deadline):
            if action == "cleanup":
                cleaning.set()
                await release.wait()
            return await super().control(action, intent, deadline=deadline)

    backend = WaitingBackend()
    install(monkeypatch, backend)
    service = ExecutionReadiness()
    task = asyncio.create_task(service.check(ReadinessRequest(distribution="Ubuntu")))
    await started.wait()
    task.cancel()
    await cleaning.wait()
    for _ in range(3):
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert backend.controls == ["cleanup", "status"]
    assert not service.cache and not service.lock.locked()


def test_api_authentication_and_fixed_request_only(workspace_tmp_path, monkeypatch):
    backend = Backend()
    install(monkeypatch, backend)
    store = RunStore(workspace_tmp_path / "db.sqlite3")
    token = "readiness-test-token-with-at-least-32-characters"
    headers = {"Authorization": "Bearer " + token}
    provider = LMStudioProvider()
    with TestClient(create_app(provider, SecretStr(token), store=store)) as client:
        assert (
            client.post("/execution/readiness", json={"distribution": "Ubuntu"}).status_code == 401
        )
        for extra in ({"argv": ["unsafe"]}, {"workspace": "C:/"}, {"profile_sha256": "b" * 64}):
            assert (
                client.post(
                    "/execution/readiness",
                    headers=headers,
                    json={"distribution": "Ubuntu", **extra},
                ).status_code
                == 422
            )
        response = client.post(
            "/execution/readiness", headers=headers, json={"distribution": "Ubuntu"}
        )
        assert response.status_code == 200 and response.json()["state"] == "ready"
        assert client.get("/runs", headers=headers).json()["items"] == []
    assert backend.launches == 1
