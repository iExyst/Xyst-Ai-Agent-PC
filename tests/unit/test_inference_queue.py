import asyncio

import pytest
from xaa.providers.lm_studio import ContextLimitError, LMStudioProvider


class BlockingProvider(LMStudioProvider):
    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def _chat(self, **kwargs):
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return {"ok": True}


async def test_oversized_context_never_enters_inference():
    provider = BlockingProvider()
    provider._context_limits["fixture"] = 2048
    try:
        with pytest.raises(ContextLimitError):
            await provider.chat(model="fixture", messages=[{"role": "user", "content": "x" * 4096}])
        assert provider.calls == 0
    finally:
        await provider.close()


async def test_cancelled_waiter_does_not_enter_inference_or_block_successor():
    provider = BlockingProvider()
    try:
        first = asyncio.create_task(provider.chat(model="fixture", messages=[]))
        await asyncio.wait_for(provider.started.wait(), 1)
        queued = asyncio.create_task(provider.chat(model="fixture", messages=[]))
        await asyncio.sleep(0)
        assert provider.calls == 1
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        provider.release.set()
        assert await asyncio.wait_for(first, 1) == {"ok": True}
        assert await asyncio.wait_for(provider.chat(model="fixture", messages=[]), 1) == {
            "ok": True
        }
        assert provider.calls == 2
    finally:
        await provider.close()


@pytest.mark.parametrize("streamed", [False, True])
async def test_output_allowance_is_reserved_before_http(streamed):
    import json

    import httpx

    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
        if streamed:
            chunks = [
                {"choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                {"choices": [], "usage": usage},
            ]
            return httpx.Response(
                200,
                text="".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
                + "data: [DONE]\n\n",
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
                ],
                "usage": usage,
            },
        )

    provider = LMStudioProvider()
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        base_url="http://127.0.0.1:1234", transport=httpx.MockTransport(handle)
    )
    provider._context_limits["fixture"] = 4096
    try:
        method = provider.chat_streamed if streamed else provider.chat
        with pytest.raises(ContextLimitError):
            await method(
                model="fixture",
                messages=[{"role": "user", "content": "small prompt"}],
                max_output_tokens=4096,
            )
        assert requests == []
        result = await method(
            model="fixture",
            messages=[{"role": "user", "content": "small prompt"}],
            max_output_tokens=32,
        )
        assert result["choices"][0]["message"]["content"] == "ok"
        assert len(requests) == 1
        assert requests[0]["max_tokens"] == 32
    finally:
        await provider.close()
