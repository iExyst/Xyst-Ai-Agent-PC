import asyncio

import pytest
from xaa.providers.inference_queue import InferenceQueue, InferenceQueueFull, InferenceQueueTimeout
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
        assert provider._inference_queue.waiting == 0
        assert provider._inference_queue.reserved_tokens > 0
        provider.release.set()
        assert await asyncio.wait_for(first, 1) == {"ok": True}
        assert provider._inference_queue.reserved_tokens == 0
        assert await asyncio.wait_for(provider.chat(model="fixture", messages=[]), 1) == {
            "ok": True
        }
        assert provider.calls == 2
    finally:
        await provider.close()


async def test_queue_is_fifo_and_bounds_waiting_request_count():
    queue = InferenceQueue(max_pending=2, max_reserved_tokens=100)
    entered = asyncio.Event()
    release = asyncio.Event()
    order = []

    async def work(number):
        async with queue.slot(1):
            order.append(number)
            if number == 1:
                entered.set()
                await release.wait()

    first = asyncio.create_task(work(1))
    await entered.wait()
    second = asyncio.create_task(work(2))
    third = asyncio.create_task(work(3))
    await asyncio.sleep(0)
    assert queue.waiting == 2
    with pytest.raises(InferenceQueueFull, match="queue is full"):
        async with queue.slot(1):
            pytest.fail("over-capacity request entered the queue")
    release.set()
    await asyncio.gather(first, second, third)
    assert order == [1, 2, 3]
    assert queue.waiting == 0
    assert queue.reserved_tokens == 0


async def test_queue_bounds_active_and_pending_token_weight():
    queue = InferenceQueue(max_pending=2, max_reserved_tokens=100)
    entered = asyncio.Event()
    release = asyncio.Event()
    queued_entered = asyncio.Event()

    async def holder():
        async with queue.slot(45):
            entered.set()
            await release.wait()

    async def pending():
        async with queue.slot(45):
            queued_entered.set()

    active = asyncio.create_task(holder())
    await entered.wait()
    queued = asyncio.create_task(pending())
    await asyncio.sleep(0)
    assert queue.waiting == 1
    with pytest.raises(InferenceQueueFull, match="estimated prompt and output"):
        async with queue.slot(11):
            pytest.fail("overweight request entered the queue")
    release.set()
    await asyncio.gather(active, queued)
    assert queued_entered.is_set()
    assert queue.reserved_tokens == 0


async def test_queue_wait_deadline_releases_reservation():
    queue = InferenceQueue(wait_timeout=0.02)
    await queue.lock.acquire()
    try:
        with pytest.raises(InferenceQueueTimeout, match="Timed out waiting"):
            async with queue.slot(25):
                pytest.fail("timed-out request entered the queue")
        assert queue.waiting == 0
        assert queue.reserved_tokens == 0
    finally:
        queue.lock.release()


async def test_read_timeout_is_actionable_and_releases_queue_for_successor():
    import httpx
    from xaa.providers.lm_studio import ProviderError

    requests = 0

    def handle(request):
        nonlocal requests
        requests += 1
        if requests == 1:
            raise httpx.ReadTimeout("synthetic local server stall")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "READY"}, "finish_reason": "stop"}
                ]
            },
        )

    provider = LMStudioProvider()
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        base_url="http://127.0.0.1:1234", transport=httpx.MockTransport(handle)
    )
    try:
        with pytest.raises(ProviderError, match="response stream timed out"):
            async for _ in provider.stream(
                model="fixture", messages=[{"role": "user", "content": "wait"}]
            ):
                pytest.fail("synthetic timeout unexpectedly yielded content")
        assert provider._inference_queue.waiting == 0
        assert provider._inference_queue.reserved_tokens == 0
        result = await provider.chat(
            model="fixture", messages=[{"role": "user", "content": "ready"}]
        )
        assert result["choices"][0]["message"]["content"] == "READY"
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
