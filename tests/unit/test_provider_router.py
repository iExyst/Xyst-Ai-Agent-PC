from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider, ModelInfo, ProviderError
from xaa.providers.openai_compatible import OpenAICompatibleProvider
from xaa.providers.router import OPENAI_MODEL_PREFIX, ProviderRouter
from xaa.storage.runs import RunStore


@pytest.mark.asyncio
async def test_router_namespaces_models_and_dispatches_to_selected_adapter() -> None:
    observed: list[tuple[str, str]] = []

    def handler(adapter: str):
        async def respond(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/models":
                return httpx.Response(200, json={"data": [{"id": f"{adapter}-model"}]})
            payload = json.loads(request.content)
            observed.append((adapter, payload["model"]))
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "done"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
                },
            )

        return respond

    lm = OpenAICompatibleProvider(
        "https://local.example.test", transport=httpx.MockTransport(handler("local"))
    )
    optional = OpenAICompatibleProvider(
        "https://remote.example.test", "secret", transport=httpx.MockTransport(handler("remote"))
    )
    router = ProviderRouter(cast(LMStudioProvider, lm), optional)
    try:
        inventory = await router.models()
        assert [model.id for model in inventory] == [
            "local-model",
            f"{OPENAI_MODEL_PREFIX}remote-model",
        ]
        await router.chat(model=inventory[0].id, messages=[{"role": "user", "content": "hi"}])
        await router.chat(model=inventory[1].id, messages=[{"role": "user", "content": "hi"}])
        assert observed == [("local", "local-model"), ("remote", "remote-model")]
    finally:
        await router.close()


@pytest.mark.asyncio
async def test_router_refuses_unknown_or_unconfigured_adapter_ids() -> None:
    lm = OpenAICompatibleProvider("https://local.example.test")
    router = ProviderRouter(cast(LMStudioProvider, lm))
    try:
        with pytest.raises(ProviderError, match="not configured"):
            await router.chat(
                model=f"{OPENAI_MODEL_PREFIX}model", messages=[{"role": "user", "content": "hi"}]
            )
        with pytest.raises(ProviderError, match="not supported"):
            await router.chat(model="invented::model", messages=[])
    finally:
        await router.close()


@pytest.mark.asyncio
async def test_replaced_client_stays_open_until_router_shutdown() -> None:
    class TrackingProvider(OpenAICompatibleProvider):
        def __init__(self, endpoint: str) -> None:
            super().__init__(endpoint)
            self.was_closed = False

        async def close(self) -> None:
            self.was_closed = True
            await super().close()

    lm = OpenAICompatibleProvider("https://local.example.test")
    optional = TrackingProvider("https://first.example.test")
    router = ProviderRouter(cast(LMStudioProvider, lm), optional)
    await router.configure_openai_compatible("https://second.example.test", "new-secret")
    assert optional.was_closed is False
    await router.close()
    assert optional.was_closed is True


def test_namespaced_provider_completes_a_durable_run_through_api_and_coordinator(
    workspace_tmp_path: Path,
) -> None:
    class LocalFixtureProvider(LMStudioProvider):
        async def models(self) -> list[ModelInfo]:
            return [ModelInfo(id="local-fixture-model")]

    seen_models: list[str] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "run-fixture-model"}]})
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        seen_models.append(body["model"])
        if body.get("stream") is True:
            content = "The fixture run completed through its selected provider."
            chunks = [
                {
                    "choices": [
                        {"delta": {"role": "assistant", "content": content}, "finish_reason": None}
                    ]
                },
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                {
                    "choices": [],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 10, "total_tokens": 22},
                },
            ]
            stream = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=stream + "data: [DONE]\n\n",
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "The fixture run completed through its selected provider.",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 10, "total_tokens": 22},
            },
        )

    token = "provider-router-api-test-token-long-enough"
    store = RunStore(workspace_tmp_path / "provider-run.sqlite3")
    lm_studio = LocalFixtureProvider()
    compatible = OpenAICompatibleProvider(
        "https://fixture.example.test",
        "fixture-secret",
        transport=httpx.MockTransport(respond),
    )
    router = ProviderRouter(lm_studio, compatible)
    app = create_app(router, SecretStr(token), store=store, stream_responses=True)
    with store.connection() as connection:
        connection.execute(
            "UPDATE provider_profiles SET endpoint=? WHERE id='openai-compatible'",
            ("https://fixture.example.test",),
        )

    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        client = cast(Any, client)
        models = client.get("/models", headers=headers)
        assert models.status_code == 200
        assert f"{OPENAI_MODEL_PREFIX}run-fixture-model" in {model["id"] for model in models.json()}
        selected = client.put(
            "/providers/openai-compatible/selection",
            headers=headers,
            json={"model": f"{OPENAI_MODEL_PREFIX}run-fixture-model"},
        )
        assert selected.status_code == 200
        created = client.post(
            "/runs",
            headers=headers,
            json={
                "objective": "Return a short result from the selected model.",
                "model": f"{OPENAI_MODEL_PREFIX}run-fixture-model",
                "max_iterations": 1,
            },
        )
        assert created.status_code == 201
        run_id = created.json()["id"]
        deadline = time.monotonic() + 5
        current = created.json()
        while time.monotonic() < deadline:
            current = client.get(f"/runs/{run_id}", headers=headers).json()
            if current["state"] in {"verifying", "completed", "failed", "blocked"}:
                break
            time.sleep(0.02)
        assert current["state"] == "verifying"
        reviewed = client.post(
            f"/runs/{run_id}/review",
            headers=headers,
            json={
                "decision": "accept",
                "expected_revision": current["revision"],
                "rationale": "The fixture returned the selected provider's result.",
                "objective_confirmed": True,
                "confirmed_criteria": [],
            },
        )
        assert reviewed.status_code == 200
        current = reviewed.json()

    assert current["state"] == "completed"
    assert current["model"] == f"{OPENAI_MODEL_PREFIX}run-fixture-model"
    assert seen_models == ["run-fixture-model"]
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT selected_model FROM provider_profiles WHERE id='openai-compatible'"
            ).fetchone()[0]
            == f"{OPENAI_MODEL_PREFIX}run-fixture-model"
        )
    assert any(
        event["event_type"] == "model.message"
        and event["payload"]["message"].get("content")
        == "The fixture run completed through its selected provider."
        for event in store.list_events(run_id)
    )
