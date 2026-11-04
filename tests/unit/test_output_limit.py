import json

import httpx
import pytest
from xaa.providers.lm_studio import LMStudioProvider, ProviderError


@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("reported", [None, 3, 4])
async def test_limit_reaches_wire_and_response_is_verified(streamed, reported):
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["max_tokens"] == 3
        usage = (
            {"prompt_tokens": 2, "completion_tokens": reported, "total_tokens": 2 + reported}
            if reported is not None
            else None
        )
        if streamed:
            assert payload["stream_options"] == {"include_usage": True}
            chunks = [
                {"choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ]
            if usage:
                chunks.append({"choices": [], "usage": usage})
            return httpx.Response(
                200,
                text="".join("data: " + json.dumps(c) + "\n\n" for c in chunks)
                + "data: [DONE]\n\n",
            )
        response = {
            "choices": [
                {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
            ]
        }
        if usage:
            response["usage"] = usage
        return httpx.Response(200, json=response)

    provider = LMStudioProvider()
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        base_url="http://127.0.0.1:1234", transport=httpx.MockTransport(handle)
    )
    try:
        method = provider.chat_streamed if streamed else provider.chat
        if reported == 3:
            result = await method(model="fixture", messages=[], max_output_tokens=3)
            assert result["choices"][0]["message"]["content"] == "ok"
        else:
            with pytest.raises(ProviderError):
                await method(model="fixture", messages=[], max_output_tokens=3)
        assert len(requests) == 1
    finally:
        await provider.close()


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, 1_000_001])
async def test_invalid_limit_never_sends_request(limit):
    provider = LMStudioProvider()
    try:
        with pytest.raises(ValueError):
            await provider.chat(model="fixture", messages=[], max_output_tokens=limit)
    finally:
        await provider.close()
