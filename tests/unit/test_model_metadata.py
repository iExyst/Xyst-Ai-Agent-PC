from __future__ import annotations

from typing import cast

import httpx
import pytest
from xaa.providers.lm_studio import LMStudioProvider, ProviderError


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


@pytest.mark.parametrize(
    "payload",
    cast(
        list[object],
        [
            [],
            {},
            {"data": {}},
            {"data": [None]},
            {"data": [{}]},
            {"data": [{"id": 7}]},
            {"data": [{"id": "  "}]},
        ],
    ),
)
@pytest.mark.asyncio
async def test_model_discovery_returns_actionable_error_for_malformed_inventory(
    payload: object,
) -> None:
    provider = LMStudioProvider()
    await provider.close()

    async def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json=payload)

    provider._client = httpx.AsyncClient(
        base_url=provider.base_url,
        transport=httpx.MockTransport(respond),
    )
    try:
        with pytest.raises(ProviderError, match="LM Studio"):
            await provider.models()
    finally:
        await provider.close()


@pytest.mark.parametrize("status_code", [404, 405, 501])
@pytest.mark.asyncio
async def test_health_reports_reachable_server_without_lm_studio_models_endpoint(
    status_code: int,
) -> None:
    provider = LMStudioProvider()
    await provider.close()

    async def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"detail": "unsupported"})

    provider._client = httpx.AsyncClient(
        base_url=provider.base_url,
        transport=httpx.MockTransport(respond),
    )
    try:
        with pytest.raises(ProviderError, match="server .* is reachable, but does not support"):
            await provider.health()
    finally:
        await provider.close()


@pytest.mark.parametrize("status_code", [401, 403])
@pytest.mark.asyncio
async def test_health_reports_rejected_lm_studio_authentication(status_code: int) -> None:
    provider = LMStudioProvider()
    await provider.close()

    async def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code)

    provider._client = httpx.AsyncClient(
        base_url=provider.base_url,
        transport=httpx.MockTransport(respond),
    )
    try:
        with pytest.raises(ProviderError, match="rejected authentication"):
            await provider.health()
    finally:
        await provider.close()


@pytest.mark.parametrize("payload", [[], {}, {"data": {}}, {"data": [None]}, {"data": [{}]}])
@pytest.mark.asyncio
async def test_health_rejects_invalid_model_inventory(payload: object) -> None:
    provider = LMStudioProvider()
    await provider.close()

    async def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    provider._client = httpx.AsyncClient(
        base_url=provider.base_url,
        transport=httpx.MockTransport(respond),
    )
    try:
        with pytest.raises(ProviderError, match="LM Studio"):
            await provider.health()
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_health_distinguishes_unreachable_lm_studio_server() -> None:
    provider = LMStudioProvider()
    await provider.close()

    async def respond(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider._client = httpx.AsyncClient(
        base_url=provider.base_url,
        transport=httpx.MockTransport(respond),
    )
    try:
        with pytest.raises(ProviderError, match="Could not connect to LM Studio"):
            await provider.health()
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_health_accepts_compatible_inventory_even_when_no_models_are_visible() -> None:
    provider = LMStudioProvider()
    await provider.close()

    async def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    provider._client = httpx.AsyncClient(
        base_url=provider.base_url,
        transport=httpx.MockTransport(respond),
    )
    try:
        assert await provider.health() is True
    finally:
        await provider.close()
