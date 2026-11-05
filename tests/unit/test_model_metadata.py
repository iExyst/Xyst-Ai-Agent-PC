from __future__ import annotations

import httpx
import pytest
from xaa.providers.lm_studio import LMStudioProvider


@pytest.mark.asyncio
async def test_model_discovery_includes_size_and_loaded_state() -> None:
    provider = LMStudioProvider()
    await provider.close()

    async def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "fixture/model"}]})
        if request.url.path == "/api/v1/models":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "key": "fixture/model",
                            "size_bytes": 2_500_000_000,
                            "max_context_length": 8192,
                            "loaded_instances": [{"id": "instance-1", "config": {}}],
                        }
                    ]
                },
            )
        return httpx.Response(404)

    provider._client = httpx.AsyncClient(
        base_url=provider.base_url,
        transport=httpx.MockTransport(respond),
    )
    models = await provider.models()
    await provider.close()

    assert len(models) == 1
    assert models[0].id == "fixture/model"
    assert models[0].size_bytes == 2_500_000_000
    assert models[0].loaded is True
    assert models[0].context_length == 8192


@pytest.mark.asyncio
async def test_model_discovery_marks_native_metadata_unavailable() -> None:
    provider = LMStudioProvider()
    await provider.close()

    async def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "legacy/model"}]})
        return httpx.Response(404)

    provider._client = httpx.AsyncClient(
        base_url=provider.base_url,
        transport=httpx.MockTransport(respond),
    )
    models = await provider.models()
    await provider.close()

    assert models[0].size_bytes is None
    assert models[0].loaded is None
