import asyncio

import pytest
from xaa.providers.lm_studio import ContextLimitError, LMStudioProvider
from xaa.providers.tokenization import PromptCount, PromptCountError


class CountedProvider(LMStudioProvider):
    def __init__(self):
        super().__init__(exact_context=True)
        self.counted = asyncio.Event()
        self.release = asyncio.Event()
        self.predictions = []
        self.unloaded = False
        self.failure = False
        self.tokens = 20

    async def _count_prompt_owned(self, request):
        assert self._inference_lock.locked()
        self.counted.set()
        await self.release.wait()
        if self.failure:
            raise PromptCountError("fixture failure")
        return PromptCount(
            model=request.model,
            prompt_tokens=self.tokens,
            context_length=1024,
            sdk_version="fixture",
        )

    async def _chat(self, **kwargs):
        assert self._inference_lock.locked()
        assert not self.unloaded
        self.predictions.append(kwargs)
        return {"ok": True}

    async def _stream(self, **kwargs):
        assert self._inference_lock.locked()
        assert not self.unloaded
        self.predictions.append(kwargs)
        yield "fixture"

    async def _unload_model(self, instance_id):
        self.unloaded = True


@pytest.mark.parametrize("streamed", [False, True])
async def test_count_inference_and_lifecycle_share_one_owner(streamed):
    provider = CountedProvider()

    async def predict():
        if streamed:
            return [
                chunk
                async for chunk in provider.stream(
                    model="fixture", messages=[{"role": "user", "content": "hello"}]
                )
            ]
        return await provider.chat(model="fixture", messages=[{"role": "user", "content": "hello"}])

    try:
        prediction = asyncio.create_task(predict())
        await provider.counted.wait()
        unload = asyncio.create_task(provider.unload_model("fixture"))
        await asyncio.sleep(0)
        assert not provider.unloaded
        provider.release.set()
        await prediction
        await unload
        assert provider.predictions[0]["max_output_tokens"] == 512
        assert provider.unloaded
    finally:
        await provider.close()


@pytest.mark.parametrize("failure", [False, True])
async def test_count_failure_or_context_exhaustion_prevents_prediction(failure):
    provider = CountedProvider()
    provider.release.set()
    provider.failure = failure
    provider.tokens = 1000
    try:
        with pytest.raises(ContextLimitError):
            await provider.chat(
                model="fixture",
                messages=[{"role": "user", "content": "hello"}],
                max_output_tokens=25,
            )
        assert not provider.predictions
        assert not provider._inference_lock.locked()
        provider.failure = False
        provider.tokens = 999
        assert await provider.chat(
            model="fixture", messages=[{"role": "user", "content": "hello"}], max_output_tokens=25
        ) == {"ok": True}
    finally:
        await provider.close()


async def test_cancelled_count_releases_owner_without_prediction():
    provider = CountedProvider()
    try:
        prediction = asyncio.create_task(
            provider.chat(model="fixture", messages=[{"role": "user", "content": "hello"}])
        )
        await provider.counted.wait()
        prediction.cancel()
        with pytest.raises(asyncio.CancelledError):
            await prediction
        assert not provider.predictions
        await asyncio.wait_for(provider.unload_model("fixture"), 1)
    finally:
        await provider.close()


async def test_streamed_default_cap_is_verified_after_assembly():
    import json

    import httpx
    from xaa.providers.lm_studio import ProviderError

    provider = CountedProvider()
    provider.release.set()
    # Use the production streaming transport rather than the fixture's raw stream.
    provider._stream = LMStudioProvider._stream.__get__(provider)
    await provider._client.aclose()

    def handle(request):
        payload = json.loads(request.content)
        assert payload["max_tokens"] == 512
        chunks = [
            {"choices": [{"index": 0, "delta": {"content": "answer"}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            {
                "choices": [],
                "usage": {"prompt_tokens": 20, "completion_tokens": 513, "total_tokens": 533},
            },
        ]
        return httpx.Response(
            200,
            text="".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
            + "data: [DONE]\n\n",
        )

    provider._client = httpx.AsyncClient(
        base_url=provider.base_url, transport=httpx.MockTransport(handle)
    )
    try:
        with pytest.raises(ProviderError, match="exceeded"):
            await provider.chat_streamed(
                model="fixture", messages=[{"role": "user", "content": "hello"}]
            )
    finally:
        await provider.close()
