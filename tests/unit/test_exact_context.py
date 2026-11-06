import asyncio

import pytest
from xaa.providers.lm_studio import ContextLimitError, LMStudioProvider
from xaa.providers.tokenization import PromptCount, PromptCountError, PromptCountRequest
from xaa.runtime.coordinator import Coordinator
from xaa.storage.runs import RunStore


class CountedProvider(LMStudioProvider):
    def __init__(self, *, exact_context=True):
        super().__init__(exact_context=exact_context)
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


async def test_inventory_websocket_count_error_retries_once_within_deadline(monkeypatch):
    from xaa.providers import tokenization

    provider = LMStudioProvider()
    request = PromptCountRequest.model_validate(
        {"model": "fixture", "messages": [{"role": "user", "content": "hello"}]}
    )
    timeouts = []
    calls = 0

    async def count_prompt(base_url, payload, credential, *, timeout):
        nonlocal calls
        assert base_url == provider.base_url
        assert payload is request
        assert credential is None
        calls += 1
        timeouts.append(timeout)
        if calls == 1:
            raise PromptCountError(
                "safe failure",
                worker_error_type="LMStudioWebsocketError",
                worker_error_stage="model_inventory",
            )
        return PromptCount(
            model="fixture", prompt_tokens=1, context_length=1024, sdk_version="fixture"
        )

    monkeypatch.setattr(tokenization, "count_prompt", count_prompt)
    try:
        result = await provider._count_prompt_owned(request)
        assert result.prompt_tokens == 1
        assert calls == 2
        assert timeouts[0] == 20
        assert 0 < timeouts[1] <= 20
    finally:
        await provider.close()


@pytest.mark.parametrize(
    ("error_type", "stage"),
    [("LMStudioServerError", "model_inventory"), ("LMStudioWebsocketError", "tokenization")],
)
async def test_count_error_outside_inventory_websocket_is_not_retried(
    monkeypatch, error_type, stage
):
    from xaa.providers import tokenization

    provider = LMStudioProvider()
    request = PromptCountRequest.model_validate(
        {"model": "fixture", "messages": [{"role": "user", "content": "hello"}]}
    )
    calls = 0

    async def count_prompt(base_url, payload, credential, *, timeout):
        nonlocal calls
        calls += 1
        raise PromptCountError(
            "safe failure", worker_error_type=error_type, worker_error_stage=stage
        )

    monkeypatch.setattr(tokenization, "count_prompt", count_prompt)
    try:
        with pytest.raises(PromptCountError):
            await provider._count_prompt_owned(request)
        assert calls == 1
    finally:
        await provider.close()


async def test_per_request_exact_admission_is_available_when_global_mode_is_off():
    provider = CountedProvider(exact_context=False)
    provider.release.set()
    admissions = []
    try:
        result = await provider.chat(
            model="fixture",
            messages=[{"role": "user", "content": "hello"}],
            require_exact_context=True,
            on_admission=lambda prompt, output: admissions.append((prompt, output)),
        )
        assert result == {"ok": True}
        assert admissions == [(20, 512)]
        assert provider.predictions[0]["max_output_tokens"] == 512
    finally:
        await provider.close()


async def test_per_request_exact_admission_covers_streaming():
    provider = CountedProvider(exact_context=False)
    provider.release.set()
    admissions = []
    try:
        chunks = [
            chunk
            async for chunk in provider.stream(
                model="fixture",
                messages=[{"role": "user", "content": "hello"}],
                require_exact_context=True,
                on_admission=lambda prompt, output: admissions.append((prompt, output)),
            )
        ]
        assert chunks == ["fixture"]
        assert admissions == [(20, 512)]
        assert provider.predictions[0]["max_output_tokens"] == 512
    finally:
        await provider.close()


async def test_per_request_exact_count_failure_blocks_before_prediction():
    provider = CountedProvider(exact_context=False)
    provider.release.set()
    provider.failure = True
    try:
        with pytest.raises(ContextLimitError, match="could not count") as failure:
            await provider.chat(
                model="fixture",
                messages=[{"role": "user", "content": "hello"}],
                require_exact_context=True,
            )
        assert failure.value.reason == "prompt_count_unavailable"
        assert not provider.predictions
    finally:
        await provider.close()


async def test_run_reports_exact_count_unavailable_instead_of_context_overflow(
    workspace_tmp_path,
):
    store = RunStore(workspace_tmp_path / "count-unavailable.sqlite3")
    run = store.create_run(
        objective="Return a short answer",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
        max_total_tokens=1000,
        pre_inference_token_reservations=True,
    )

    class UnavailableCounter(LMStudioProvider):
        async def _count_prompt_owned(self, request):
            raise PromptCountError(
                "fixture failure",
                worker_error_type="LMStudioServerError",
                worker_error_stage="tokenization",
                worker_error_code="generic.tokenizeFailed",
            )

        async def _chat(self, **kwargs):
            raise AssertionError("Uncounted inference must not be sent")

    provider = UnavailableCounter(exact_context=False)
    try:
        await Coordinator(store, provider).execute(run["id"])
    finally:
        await provider.close()

    blocked = [
        event
        for event in store.list_events(run["id"])
        if event["event_type"] == "run.state_changed" and event["payload"].get("to") == "blocked"
    ]
    assert blocked[-1]["payload"]["reason"] == "prompt_count_unavailable"
    events = store.list_events(run["id"])
    assert not any(event["event_type"] == "model.message" for event in events)
    failure = next(event for event in events if event["event_type"] == "context.admission_failed")
    assert failure["payload"] == {
        "reason": "prompt_count_unavailable",
        "counter_error_type": "LMStudioServerError",
        "counter_error_stage": "tokenization",
        "counter_error_code": "generic.tokenizeFailed",
    }


async def test_conservative_context_estimate_bounds_image_data_url():
    provider = LMStudioProvider()
    provider._context_limits["fixture"] = 10_000
    try:
        provider._check_context(
            "fixture",
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe this"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64," + "A" * 4_000_000},
                        },
                    ],
                }
            ],
            max_output_tokens=512,
        )
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
