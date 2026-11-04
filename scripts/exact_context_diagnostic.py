"""Real SDK context admission for streamed inference and over-context rejection."""

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from xaa.providers.lm_studio import ContextLimitError, LMStudioProvider
from xaa.providers.tokenization import PromptCount, PromptCountRequest


class AuditedProvider(LMStudioProvider):
    def __init__(self) -> None:
        super().__init__(exact_context=True)
        self.counts: list[PromptCount] = []
        self.predictions = 0

    async def _count_prompt_owned(self, request: PromptCountRequest) -> PromptCount:
        count = await super()._count_prompt_owned(request)
        self.counts.append(count)
        return count

    async def _stream(self, **kwargs: Any):
        self.predictions += 1
        async for chunk in super()._stream(**kwargs):
            yield chunk

    async def _chat(self, **kwargs: Any) -> dict[str, Any]:
        self.predictions += 1
        return await super()._chat(**kwargs)


async def main() -> None:
    root = (Path(".acceptance") / str(uuid4())).resolve()
    root.mkdir(parents=True)
    provider = AuditedProvider()
    instance = None
    report: dict[str, Any] = {"passed": False}
    try:
        instance = await provider.load_model("qwen/qwen3.5-9b", context_length=16384)
        messages = [{"role": "user", "content": "Reply with exactly OK."}]
        for streamed in (False, True):
            try:
                if streamed:
                    async for _ in provider.stream(
                        model=instance, messages=messages, max_output_tokens=16384
                    ):
                        pass
                else:
                    await provider.chat(model=instance, messages=messages, max_output_tokens=16384)
            except ContextLimitError:
                pass
            else:
                raise RuntimeError("Over-context request admitted")
        assert provider.predictions == 0
        response = await provider.chat_streamed(
            model=instance, messages=messages, max_output_tokens=512
        )
        assert provider.counts[-1].prompt_tokens == response["usage"]["prompt_tokens"]
        assert provider.predictions == 1
        report.update(
            passed=True,
            model=instance,
            context_length=16384,
            over_context_rejected_before_http=2,
            predictions=provider.predictions,
            sdk_prompt_tokens=provider.counts[-1].prompt_tokens,
            http_prompt_tokens=response["usage"]["prompt_tokens"],
            finish_reason=response["choices"][0]["finish_reason"],
        )
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
