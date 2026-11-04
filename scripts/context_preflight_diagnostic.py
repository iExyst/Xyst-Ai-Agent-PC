"""Real context/output admission check; rejected requests must never reach inference."""

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from xaa.providers.lm_studio import ContextLimitError, LMStudioProvider


class ObservedProvider(LMStudioProvider):
    def __init__(self) -> None:
        super().__init__()
        self.sent: list[int] = []

        async def observe(request: httpx.Request) -> None:
            if request.url.path == "/v1/chat/completions":
                self.sent.append(json.loads(request.content)["max_tokens"])

        self._client.event_hooks["request"].append(observe)


async def main() -> None:
    root = (Path(".acceptance") / str(uuid4())).resolve()
    root.mkdir(parents=True)
    provider = ObservedProvider()
    model = "qwen/qwen3.5-9b"
    instance = None
    sent = provider.sent
    report: dict[str, Any] = {"passed": False, "model": model, "loaded_context": 16384}
    try:
        await provider.models()
        instance = await provider.load_model(model, context_length=16384)
        rejected: list[str] = []
        for name, method in [("normal", provider.chat), ("streamed", provider.chat_streamed)]:
            try:
                await method(
                    model=model,
                    messages=[{"role": "user", "content": "Say hello."}],
                    max_output_tokens=16384,
                )
            except ContextLimitError:
                rejected.append(name)
        assert sent == []
        response = await provider.chat(
            model=model, messages=[{"role": "user", "content": "Say hello."}], max_output_tokens=32
        )
        report.update(
            rejected_modes=rejected,
            inference_output_caps=sent,
            accepted_usage=response.get("usage"),
        )
        report["passed"] = (
            rejected == ["normal", "streamed"]
            and sent == [32]
            and response["usage"]["completion_tokens"] <= 32
        )
        if not report["passed"]:
            raise RuntimeError("Context/output preflight acceptance failed.")
    finally:
        try:
            if instance is not None:
                await provider.unload_model(instance)
        finally:
            await provider.close()
            (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps({**report, "evidence": str(root)}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
