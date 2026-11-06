from __future__ import annotations

import asyncio
import threading
import zipfile
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider, ModelCapabilities, ModelInfo, ProviderError
from xaa.storage.runs import RunStore
from xaa.version import APP_VERSION


class FakeProvider(LMStudioProvider):
    async def diagnose_tools(self, model, *, request=None):
        return True

    async def health(self) -> bool:
        return True

    async def models(self) -> list[ModelInfo]:
        return [ModelInfo(id="fixture/model")]

    async def stream(self, *, model: str, messages: list[dict[str, Any]]) -> AsyncIterator[str]:
        yield '{"choices":[{"delta":{"content":"hello"}}]}'
        yield '{"choices":[{"delta":{"content":" world"}}]}'

    async def close(self) -> None:
        return None


TOKEN = "a-test-token-that-is-long-enough-to-pass-validation"


def test_openapi_advertises_packaged_engine_version(workspace_tmp_path) -> None:
    app = create_app(
        FakeProvider(), SecretStr(TOKEN), store=RunStore(workspace_tmp_path / "api.sqlite3")
    )
    assert app.version == APP_VERSION


def test_public_tool_result_api_is_authenticated_and_excludes_internal_data(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "tool-result-api.sqlite3")
    run_id = store.create_run(
        objective="Read a file", acceptance_criteria=[], workspace=None, mode="observe", model=None
    )["id"]
    token = store.acquire_lease(run_id, "worker")
    assert token
    store.record_tool_intent(
        run_id,
        token,
        call_id="public-call",
        tool="workspace.read_file",
        version="1",
        arguments={"path": "secret.txt"},
        idempotency_key="public-action",
    )
    store.record_tool_result(run_id, token, "public-call", {"content": "visible result"})
    store.record_tool_intent(
        run_id,
        token,
        call_id="internal-call",
        tool="internal.check",
        version="1",
        arguments={},
        idempotency_key="internal-action",
        internal=True,
    )
    store.record_tool_result(run_id, token, "internal-call", {"content": "private result"})
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(FakeProvider(), SecretStr(TOKEN), store=store)) as client:
        public = client.get(f"/runs/{run_id}/tools/public-call/result", headers=headers)
        private = client.get(f"/runs/{run_id}/tools/internal-call/result", headers=headers)
        unauthorized = client.get(f"/runs/{run_id}/tools/public-call/result")
    assert public.status_code == 200
    assert public.json() == {"result": '{"content": "visible result"}'}
    assert private.status_code == 200
    assert private.json() == {"result": None}
    assert unauthorized.status_code == 401


class WaitingProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.stopped = threading.Event()

    async def chat(self, **kwargs):
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.stopped.set()


def test_api_dispatches_and_pauses_active_worker(workspace_tmp_path):
    provider = WaitingProvider()
    store = RunStore(workspace_tmp_path / "dispatch.sqlite3")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(provider, SecretStr(TOKEN), store=store)) as client:
        response = client.post(
            "/runs", headers=headers, json={"objective": "Wait", "model": "fixture/model"}
        )
        assert response.status_code == 201
        run_id = response.json()["id"]
        assert provider.started.wait(2)
        paused = client.post(f"/runs/{run_id}/pause", headers=headers)
        assert paused.status_code == 200
        assert paused.json()["state"] == "paused"
        assert provider.stopped.wait(2)
        assert store.get_run(run_id)["lease_owner"] is None


def test_api_requires_authentication(workspace_tmp_path) -> None:
    client = TestClient(
        create_app(
            FakeProvider(), SecretStr(TOKEN), store=RunStore(workspace_tmp_path / "api.sqlite3")
        )
    )
    assert client.get("/models").status_code == 401
    assert (
        client.get("/models", headers={"Authorization": f"Bearer {TOKEN}"}).json()[0]["id"]
        == "fixture/model"
    )


def test_authenticated_backup_endpoint_returns_verified_artifact_bundle(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "backup.sqlite3")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(FakeProvider(), SecretStr(TOKEN), store=store)) as client:
        response = client.post("/backups", headers=headers)
    assert response.status_code == 201
    bundle = response.json()["path"]
    assert bundle.endswith(".xaa-backup")
    with zipfile.ZipFile(bundle) as archive:
        assert archive.testzip() is None
        assert {"manifest.json", "xaa.sqlite3"}.issubset(archive.namelist())


def test_stream_emits_ordered_events(workspace_tmp_path) -> None:
    client = TestClient(
        create_app(
            FakeProvider(), SecretStr(TOKEN), store=RunStore(workspace_tmp_path / "api.sqlite3")
        )
    )
    response = client.post(
        "/chat/stream",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"model": "fixture/model", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 200
    assert "id: 1\nevent: token" in response.text
    assert "id: 2\nevent: token" in response.text
    assert "event: done" in response.text


def test_api_rejects_short_token() -> None:
    with pytest.raises(ValueError, match="at least 32"):
        create_app(FakeProvider(), SecretStr("short"))


def test_memory_controls_are_authenticated_and_remove_search_results(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "memory-api.sqlite3")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(FakeProvider(), SecretStr(TOKEN), store=store)) as client:
        assert client.get("/memory").status_code == 401
        assert (
            client.post(
                "/memory", headers=headers, json={"content": "Python", "provenance": "system"}
            ).status_code
            == 422
        )
        response = client.post(
            "/memory", headers=headers, json={"content": "Prefer Python examples"}
        )
        assert response.status_code == 201
        memory_id = response.json()["id"]
        assert response.json()["provenance"] == "user"
        assert client.get("/memory?q=Python", headers=headers).json()
        assert (
            client.post(
                f"/memory/{memory_id}",
                headers=headers,
                json={"content": "Prefer TypeScript examples"},
            ).status_code
            == 200
        )
        assert client.get("/memory?q=Python", headers=headers).json() == []
        assert len(client.get("/memory/export", headers=headers).json()) == 1
        assert client.delete(f"/memory/{memory_id}", headers=headers).status_code == 204
        assert client.get("/memory?q=TypeScript", headers=headers).json() == []


def test_artifact_download_requires_auth_and_rejects_tampering(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "artifacts.sqlite3")
    run_id = store.create_run(
        objective="Inspect", acceptance_criteria=[], workspace=None, mode="observe", model=None
    )["id"]
    token = store.acquire_lease(run_id, "worker")
    root = workspace_tmp_path / "artifacts"
    root.mkdir()
    artifact = root / "output.txt"
    artifact.write_bytes(b"verified output")
    metadata = store.register_artifact(run_id, token, artifact.name, provenance="test")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(FakeProvider(), SecretStr(TOKEN), store=store)) as client:
        route = f"/artifacts/{metadata['id']}"
        assert client.get(route).status_code == 401
        assert client.get(route, headers=headers).content == b"verified output"
        assert (
            client.get(f"/runs/{run_id}/artifacts", headers=headers).json()[0]["id"]
            == metadata["id"]
        )
        png = root / "screenshot.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        image = store.register_artifact(
            run_id, token, png.name, provenance="browser.screenshot", media_type="image/png"
        )
        image_response = client.get(f"/artifacts/{image['id']}", headers=headers)
        assert image_response.content == png.read_bytes()
        assert image_response.headers["content-type"] == "image/png"
        assert image["media_type"] == "image/png"
        artifact.write_bytes(b"tampered")
        assert client.get(route, headers=headers).status_code == 409


def test_run_image_is_validated_stored_as_artifact_and_reconstructed(workspace_tmp_path):
    import base64

    store = RunStore(workspace_tmp_path / "run-image.sqlite3")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    png = b"\x89PNG\r\n\x1a\nfixture-image"
    encoded = "data:image/png;base64," + base64.b64encode(png).decode("ascii")

    class VisionProvider(FakeProvider):
        async def models(self) -> list[ModelInfo]:
            return [ModelInfo(id="fixture", capabilities=ModelCapabilities(vision=True))]

    with TestClient(create_app(VisionProvider(), SecretStr(TOKEN), store=store)) as client:
        bad = client.post(
            "/runs",
            headers=headers,
            json={
                "objective": "Inspect",
                "model": "fixture",
                "image": "data:image/jpeg;base64,SGVsbG8=",
            },
        )
        assert bad.status_code == 409
        response = client.post(
            "/runs",
            headers=headers,
            json={"objective": "Describe this image", "model": "fixture", "image": encoded},
        )
        assert response.status_code == 201
        run_id = response.json()["id"]
        assert store.image_data_url(run_id) == encoded
        artifacts = client.get(f"/runs/{run_id}/artifacts", headers=headers).json()
        assert len(artifacts) == 1
        assert artifacts[0]["media_type"] == "image/png"
        event = next(
            item
            for item in store.public_events(run_id)
            if item["event_type"] == "run.image_attached"
        )
        assert set(event["payload"]) == {"artifact_id", "media_type"}
        metadata = store.get_artifact(artifacts[0]["id"])
        assert metadata
        (workspace_tmp_path / "artifacts" / metadata["storage_key"]).write_bytes(png + b"tamper")
        with pytest.raises(ValueError, match="integrity"):
            store.image_data_url(run_id)

    nonvision_store = RunStore(workspace_tmp_path / "no-vision.sqlite3")
    with TestClient(create_app(FakeProvider(), SecretStr(TOKEN), store=nonvision_store)) as client:
        rejected = client.post(
            "/runs",
            headers=headers,
            json={"objective": "Describe image", "model": "fixture/model", "image": encoded},
        )
        assert rejected.status_code == 409


def test_run_text_document_is_extracted_and_persisted_for_recovery(workspace_tmp_path):
    import base64

    store = RunStore(workspace_tmp_path / "run-document.sqlite3")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    source = b"# Field notes\nThe sample valve is marked blue."
    encoded = "data:text/markdown;base64," + base64.b64encode(source).decode("ascii")
    with TestClient(create_app(FakeProvider(), SecretStr(TOKEN), store=store)) as client:
        response = client.post(
            "/runs",
            headers=headers,
            json={"objective": "Summarize this attachment", "document": encoded},
        )
        assert response.status_code == 201
        run_id = response.json()["id"]
        assert store.document_text(run_id) == source.decode("utf-8")
        artifacts = client.get(f"/runs/{run_id}/artifacts", headers=headers).json()
        assert {item["provenance"] for item in artifacts} == {
            "user.input_document",
            "user.input_document_text",
        }
        original = next(item for item in artifacts if item["provenance"] == "user.input_document")
        downloaded = client.get(f"/artifacts/{original['id']}", headers=headers)
        assert downloaded.content == source
        assert downloaded.headers["content-type"].split(";", 1)[0] == "text/markdown"
        with store._connection() as connection:
            connection.execute(
                "UPDATE artifacts SET sha256=? WHERE run_id=? AND provenance='user.input_document_text'",
                ("0" * 64, run_id),
            )
        with pytest.raises(ValueError, match="integrity"):
            store.document_text(run_id)


def test_shutdown_pauses_worker_before_signalling_server(workspace_tmp_path):
    provider = WaitingProvider()
    store = RunStore(workspace_tmp_path / "shutdown.sqlite3")
    stopped = threading.Event()
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(
        create_app(provider, SecretStr(TOKEN), store=store, shutdown_callback=stopped.set)
    ) as client:
        assert client.post("/shutdown").status_code == 401
        run_id = client.post(
            "/runs", headers=headers, json={"objective": "Wait", "model": "fixture"}
        ).json()["id"]
        assert provider.started.wait(2)
        assert client.post("/shutdown", headers=headers).json()["status"] == "stopping"
        assert stopped.is_set()
        assert provider.stopped.is_set()
        assert store.get_run(run_id)["state"] == "paused"
        assert store.get_run(run_id)["lease_owner"] is None


def test_public_run_record_does_not_expose_worker_lease(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "private-lease.sqlite3")
    run = store.create_run(
        objective="Inspect", acceptance_criteria=[], workspace=None, mode="observe", model=None
    )
    token = store.acquire_lease(run["id"], "worker")
    assert token
    with TestClient(create_app(FakeProvider(), SecretStr(TOKEN), store=store)) as client:
        response = client.get(f"/runs/{run['id']}", headers={"Authorization": f"Bearer {TOKEN}"})
        assert response.status_code == 200
        assert token not in response.text
        assert "lease_owner" not in response.json()
        assert response.json()["limits"]["max_iterations"] == 8


def test_run_trajectory_export_is_authenticated_provenance_rich_and_redacted(workspace_tmp_path):
    secret = "fixture-secret-value-1234"

    class Credentials:
        def get(self, profile="lm-studio"):
            return secret if profile == "brave-search" else None

        def set(self, _token, _profile="lm-studio"):
            return None

        def delete(self, _profile="lm-studio"):
            return None

    store = RunStore(workspace_tmp_path / "trajectory.sqlite3")
    run = store.create_run(
        objective="Check redacted export",
        acceptance_criteria=["secret absent"],
        workspace=None,
        mode="observe",
        model="fixture/model",
    )
    with store.connection() as connection:
        RunStore.record_event(
            connection,
            run["id"],
            "model.message",
            {
                "message": {
                    "role": "assistant",
                    "content": f"stored {secret}; Bearer abc.def; api_key=inline-secret",
                }
            },
        )
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(
        create_app(
            FakeProvider(),
            SecretStr(TOKEN),
            store=store,
            credential_store=Credentials(),
        )
    ) as client:
        route = f"/runs/{run['id']}/export"
        assert client.get(route).status_code == 401
        response = client.get(route, headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "attachment;" in response.headers["content-disposition"]
    assert secret.encode() not in response.content
    exported = response.json()
    assert exported["format"] == "xaa-trajectory"
    assert exported["run"]["id"] == run["id"]
    message_event = next(
        event for event in exported["events"] if event["event_type"] == "model.message"
    )
    event_text = message_event["payload"]["message"]["content"]
    assert "[REDACTED]" in event_text
    assert "inline-secret" not in event_text
    assert "abc.def" not in event_text


def test_run_trajectory_export_enforces_event_and_byte_limits(workspace_tmp_path, monkeypatch):
    from xaa.storage import trajectory

    store = RunStore(workspace_tmp_path / "trajectory-limits.sqlite3")
    run = store.create_run(
        objective="Bound export",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model=None,
    )
    monkeypatch.setattr(trajectory, "MAX_TRAJECTORY_EVENTS", 0)
    with pytest.raises(ValueError, match="event export limit"):
        trajectory.export_trajectory(store, run["id"])
    monkeypatch.setattr(trajectory, "MAX_TRAJECTORY_EVENTS", 10)
    monkeypatch.setattr(trajectory, "MAX_TRAJECTORY_BYTES", 1)
    with pytest.raises(ValueError, match="size limit"):
        trajectory.export_trajectory(store, run["id"])


def test_run_web_host_grant_is_authenticated_validated_and_durable(workspace_tmp_path):
    database = workspace_tmp_path / "web-grant.sqlite3"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(
        create_app(FakeProvider(), SecretStr(TOKEN), store=RunStore(database))
    ) as client:
        created = client.post(
            "/runs",
            headers=headers,
            json={
                "objective": "Read the docs",
                "approved_web_hosts": ["Docs.Example.com."],
                "approved_web_search": True,
                "approved_browser": True,
            },
        )
        assert created.status_code == 201
        run_id = created.json()["id"]
        assert created.json()["approved_web_hosts"] == ["docs.example.com"]
        assert created.json()["approved_web_search"] is True
        assert created.json()["approved_browser"] is True
        events = client.get(f"/runs/{run_id}/events", headers=headers).json()
        assert events[-1]["event_type"] == "web.access_granted"
        assert events[-1]["payload"] == {
            "hosts": ["docs.example.com"],
            "search": True,
            "browser": True,
            "actor": "local_user",
        }
        invalid = client.post(
            "/runs",
            headers=headers,
            json={"objective": "Bad", "approved_web_hosts": ["https://example.com/path"]},
        )
        assert invalid.status_code == 422
        assert client.get("/runs", headers=headers).status_code == 200

    recovered = RunStore(database).get_run(run_id)
    assert recovered is not None
    assert recovered["approved_web_hosts"] == ["docs.example.com"]
    assert recovered["approved_web_search"] is True
    assert recovered["approved_browser"] is True


def test_failed_tool_diagnostic_is_reported_without_enabling_tools(workspace_tmp_path):
    class UnsupportedProvider(FakeProvider):
        async def diagnose_tools(self, model, *, request=None):
            raise ProviderError("Selected model did not return a structured tool call.")

    store = RunStore(workspace_tmp_path / "diagnostics.sqlite3")
    with TestClient(create_app(UnsupportedProvider(), SecretStr(TOKEN), store=store)) as client:
        response = client.post(
            "/providers/lm-studio/diagnostics",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={"model": "fixture"},
        )
        assert response.status_code == 422
        assert "structured tool call" in response.json()["detail"]


def test_durable_event_stream_reconnects_from_last_event_id(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "events.sqlite3")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(FakeProvider(), SecretStr(TOKEN), store=store)) as client:
        run_id = client.post("/runs", headers=headers, json={"objective": "Observe"}).json()["id"]
        client.post(f"/runs/{run_id}/cancel", headers=headers)
        response = client.get(
            f"/runs/{run_id}/events/stream", headers={**headers, "Last-Event-ID": "1"}
        )
        assert response.status_code == 200
        assert "id: 1\n" not in response.text
        assert "id: 2\nevent: run_event" in response.text
        assert "cancelled" in response.text
        assert "event: done" in response.text


def test_run_api_replays_durable_events_after_restart(workspace_tmp_path) -> None:
    database = workspace_tmp_path / "api.sqlite3"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(
        create_app(FakeProvider(), SecretStr(TOKEN), store=RunStore(database))
    ) as client:
        response = client.post("/runs", headers=headers, json={"objective": "Do a safe task"})
        assert response.status_code == 201
        run_id = response.json()["id"]
        assert response.json()["state"] == "queued"
        assert client.post(f"/runs/{run_id}/pause", headers=headers).json()["state"] == "paused"
        assert client.get(f"/runs/{run_id}/events", headers=headers).json()[-1]["sequence"] == 2

    with TestClient(
        create_app(FakeProvider(), SecretStr(TOKEN), store=RunStore(database))
    ) as recovered:
        assert recovered.get(f"/runs/{run_id}", headers=headers).json()["state"] == "paused"
        assert recovered.post(f"/runs/{run_id}/resume", headers=headers).json()["state"] == "queued"


def test_token_settings_survive_public_create_read_and_control(workspace_tmp_path):
    provider = WaitingProvider()
    store = RunStore(workspace_tmp_path / "token-contract.sqlite3")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    expected = {
        "max_iterations": 8,
        "max_seconds": 900,
        "max_total_tokens": 5000,
        "max_output_tokens": 128,
    }
    with TestClient(create_app(provider, SecretStr(TOKEN), store=store)) as client:
        response = client.post(
            "/runs",
            headers=headers,
            json={"objective": "Wait", "model": "fixture/model", **expected},
        )
        assert response.status_code == 201
        identity = response.json()["id"]
        assert response.json()["limits"] == expected
        assert provider.started.wait(2)
        assert client.get(f"/runs/{identity}", headers=headers).json()["limits"] == expected
        assert client.post(f"/runs/{identity}/pause", headers=headers).json()["limits"] == expected
        for key in ["max_total_tokens", "max_output_tokens"]:
            invalid = client.post(
                "/runs",
                headers=headers,
                json={"objective": "Invalid", "model": "fixture/model", key: True},
            )
            assert invalid.status_code == 422


def test_authenticated_token_count_contract_and_safe_failure(workspace_tmp_path):
    from xaa.providers.tokenization import PromptCount, PromptCountError

    class Counter(FakeProvider):
        async def count_prompt(self, request):
            if request.model == "missing":
                raise PromptCountError("Select an already-loaded model.")
            assert request.messages[0].content == "Count this fixture"
            return PromptCount(
                model=request.model, prompt_tokens=12, context_length=4096, sdk_version="fixture"
            )

    store = RunStore(workspace_tmp_path / "count-api.sqlite3")
    client = TestClient(create_app(Counter(), SecretStr(TOKEN), store=store))
    body = {
        "model": "loaded-instance",
        "messages": [{"role": "user", "content": "Count this fixture"}],
    }
    path = "/providers/lm-studio/token-count"
    assert client.post(path, json=body).status_code == 401
    headers = {"Authorization": f"Bearer {TOKEN}"}
    response = client.post(path, json=body, headers=headers)
    assert response.status_code == 200
    assert response.json() == {
        "model": "loaded-instance",
        "prompt_tokens": 12,
        "context_length": 4096,
        "sdk_version": "fixture",
    }
    failed = client.post(path, json={**body, "model": "missing"}, headers=headers)
    assert failed.status_code == 422
    assert "Count this fixture" not in failed.text
    assert (
        client.post(path, json={**body, "api_token": "not-allowed"}, headers=headers).status_code
        == 422
    )
