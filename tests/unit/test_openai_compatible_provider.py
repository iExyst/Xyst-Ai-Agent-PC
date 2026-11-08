from __future__ import annotations

import json

import httpx
import pytest
from xaa.providers.lm_studio import ProviderError
from xaa.providers.openai_compatible import OpenAICompatibleProvider
from xaa.providers.tokenization import PromptCountError, PromptCountRequest


def test_openai_compatible_provider_requires_secure_explicit_origin() -> None:
    assert (
        OpenAICompatibleProvider.validate_endpoint("https://api.example.test")
        == "https://api.example.test"
    )
    assert (
        OpenAICompatibleProvider.validate_endpoint("http://127.0.0.1:9000/")
        == "http://127.0.0.1:9000"
    )
    assert OpenAICompatibleProvider.validate_endpoint("http://[::1]:9000") == "http://[::1]:9000"
    assert (
        OpenAICompatibleProvider.validate_endpoint("http://localhost:9000")
        == "http://127.0.0.1:9000"
    )
    for endpoint in (
        "http://api.example.test",
        "https://user:secret@api.example.test",
        "https://api.example.test/v1",
        "https://api.example.test?tenant=other",
    ):
        with pytest.raises(ValueError):
            OpenAICompatibleProvider.validate_endpoint(endpoint)


@pytest.mark.asyncio
async def test_model_inventory_and_tool_diagnostic_use_compatible_api() -> None:
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"] == "Bearer fixture-secret"
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={"data": [{"id": "fixture/model"}, {"id": "x" * 238}]},
            )
        body = json.loads(request.content)
        assert body["model"] == "fixture/model"
        assert "max_tokens" not in body
        if body.get("stream"):
            assert body["max_completion_tokens"] == 8
            assert body["stream_options"] == {"include_usage": True}
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=(
                    'data: {"choices":[{"delta":{"role":"assistant","content":"Hello"},"finish_reason":null}]}\n\n'
                    'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
                    'data: {"choices":[],"usage":{"prompt_tokens":4,"completion_tokens":1,"total_tokens":5}}\n\n'
                    "data: [DONE]\n\n"
                ),
            )
        assert body["max_completion_tokens"] == 512
        if body.get("tools"):
            prompt = body["messages"][0]["content"]
            nonce = prompt.split("value ", 1)[1].split(",", 1)[0]
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-fixture",
                        "type": "function",
                        "function": {"name": "xaa_echo", "arguments": json.dumps({"value": nonce})},
                    }
                ],
            }
            finish_reason = "tool_calls"
        else:
            nonce = body["messages"][-1]["content"]
            message = {"role": "assistant", "content": nonce}
            finish_reason = "stop"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": message, "finish_reason": finish_reason}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
            },
        )

    provider = OpenAICompatibleProvider(
        "https://api.example.test",
        "fixture-secret",
        transport=httpx.MockTransport(respond),
    )
    try:
        models = await provider.models()
        assert [item.id for item in models] == ["fixture/model"]
        assert models[0].capabilities.vision is False
        assert await provider.diagnose_tools(models[0].id) is True
        streamed = await provider.chat_streamed(
            model=models[0].id,
            messages=[{"role": "user", "content": "Say hello"}],
            max_output_tokens=8,
            on_content=lambda _text: None,
        )
        assert streamed["choices"][0]["message"]["content"] == "Hello"
        assert [request.url.path for request in requests] == [
            "/v1/models",
            "/v1/chat/completions",
            "/v1/chat/completions",
            "/v1/chat/completions",
        ]
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_provider_fails_closed_for_lm_studio_only_features() -> None:
    provider = OpenAICompatibleProvider("https://api.example.test")
    try:
        with pytest.raises(PromptCountError, match="Exact token counting is unavailable"):
            await provider.count_prompt(
                PromptCountRequest(
                    model="fixture/model",
                    messages=[{"role": "user", "content": "hello"}],
                )
            )
        with pytest.raises(ProviderError, match="does not expose model loading"):
            await provider.load_model("fixture/model")
        with pytest.raises(ProviderError, match="Scanned PDF OCR requires LM Studio"):
            await provider.transcribe_scanned_pages(
                model="fixture/model", objective="Read text", pages=[(1, b"png")]
            )
    finally:
        await provider.close()
