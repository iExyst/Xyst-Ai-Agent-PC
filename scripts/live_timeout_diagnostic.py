"""Verify a live LM Studio response timeout releases provider admission."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
from xaa.providers.lm_studio import LMStudioProvider, ProviderError


class TimeoutDiagnosticProvider(LMStudioProvider):
    def __init__(self, base_url: str) -> None:
        super().__init__(base_url)
        self._default_timeout = self._client.timeout

    def set_read_timeout(self, seconds: float) -> None:
        self._client.timeout = httpx.Timeout(60.0, connect=3.0, read=seconds)

    def restore_timeout(self) -> None:
        self._client.timeout = self._default_timeout

    def observe_response_headers(self, event: asyncio.Event) -> None:
        async def observe(response: httpx.Response) -> None:
            if response.request.url.path == "/v1/chat/completions":
                event.set()

        self._client.event_hooks["response"].append(observe)

    @property
    def queue_waiters(self) -> int:
        return self._inference_queue.waiting

    @property
    def queue_reserved_tokens(self) -> int:
        return self._inference_queue.reserved_tokens


async def main() -> None:
    provider = TimeoutDiagnosticProvider(os.environ.get("LM_STUDIO_URL", "http://127.0.0.1:1234"))
    model = os.environ.get("LM_STUDIO_MODEL", "qwen2.5-0.5b-instruct")
    instance: str | None = None
    response_headers = asyncio.Event()
    chunks = 0
    outcome = ""
    diagnostic_messages = [
        {
            "role": "system",
            "content": "You are a diagnostic assistant. Respond with exactly READY and no other text.",
        },
        {"role": "user", "content": "Return the diagnostic response now."},
    ]

    try:
        await provider.health()
        inventory = await provider.models()
        if not any(item.id == model for item in inventory):
            raise RuntimeError(f"Model {model!r} is not installed; no download will be attempted.")
        instance = await provider.load_model(model)
        baseline = await provider.chat(model=model, messages=diagnostic_messages)
        baseline_answer = baseline["choices"][0]["message"].get("content", "")
        if baseline_answer.strip() != "READY":
            raise RuntimeError(
                f"The selected model failed the baseline response check: {baseline_answer!r}"
            )
        provider.observe_response_headers(response_headers)
        provider.set_read_timeout(0.1)
        prompt = [
            {
                "role": "user",
                "content": "Write a detailed explanation of arithmetic, using many examples. " * 40,
            }
        ]
        try:
            async for _ in provider.stream(model=model, messages=prompt, max_output_tokens=512):
                chunks += 1
        except ProviderError as exc:
            outcome = str(exc)
        else:
            raise RuntimeError("The deliberately short live response-read timeout did not fire.")

        if "timed out" not in outcome.casefold() or not response_headers.is_set():
            raise RuntimeError(
                "The live request did not demonstrate a response-body timeout after "
                f"receiving server headers: {outcome}"
            )
        if provider.queue_waiters != 0 or provider.queue_reserved_tokens != 0:
            raise RuntimeError("Timed-out inference retained its queue reservation.")

        provider.restore_timeout()
        successor = await asyncio.wait_for(
            provider.chat(model=model, messages=diagnostic_messages), timeout=60
        )
        successor_answer = successor["choices"][0]["message"].get("content", "")
        passed = successor_answer.strip() == "READY"
        report = {
            "passed": passed,
            "model": model,
            "baseline_answer": baseline_answer,
            "read_timeout_seconds": 0.1,
            "server_response_headers_received": response_headers.is_set(),
            "chunks_before_timeout": chunks,
            "timeout_error": outcome,
            "queue_waiters_after_timeout": provider.queue_waiters,
            "queue_reserved_tokens_after_timeout": provider.queue_reserved_tokens,
            "successor_answer": successor_answer,
        }
        evidence = Path(".acceptance") / str(uuid4())
        evidence.mkdir(parents=True)
        (evidence / "timeout.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({**report, "evidence": str(evidence)}, indent=2))
        if not passed:
            raise RuntimeError("LM Studio did not recover with the expected successor response.")
    finally:
        provider.restore_timeout()
        try:
            if instance is not None:
                await provider.unload_model(instance)
        finally:
            await provider.close()


if __name__ == "__main__":
    asyncio.run(main())
