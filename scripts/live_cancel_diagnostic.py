"""Cancel live inference and prove a successor request can acquire the queue."""

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

from xaa.providers.lm_studio import LMStudioProvider


async def main() -> None:
    provider = LMStudioProvider(os.environ.get("LM_STUDIO_URL", "http://127.0.0.1:1234"))
    model = os.environ.get("LM_STUDIO_MODEL", "qwen3-0.6b")
    instance = None
    started = asyncio.Event()
    chunks = 0
    diagnostic_messages = [
        {
            "role": "system",
            "content": "You are a diagnostic assistant. Respond with exactly READY and no other text.",
        },
        {"role": "user", "content": "Return the diagnostic response now."},
    ]

    async def consume() -> None:
        nonlocal chunks
        async for _ in provider.stream(
            model=model,
            messages=[{"role": "user", "content": "Explain arithmetic in great detail."}],
        ):
            chunks += 1
            started.set()

    task = None
    try:
        instance = await provider.load_model(model)
        baseline_response = await provider.chat(model=model, messages=diagnostic_messages)
        baseline_answer = baseline_response["choices"][0]["message"].get("content", "")
        task = asyncio.create_task(consume())
        await asyncio.wait_for(started.wait(), timeout=30)
        task.cancel()
        cancelled = False
        try:
            await asyncio.wait_for(task, timeout=5)
        except asyncio.CancelledError:
            cancelled = True
        response = await asyncio.wait_for(
            provider.chat(model=model, messages=diagnostic_messages),
            timeout=30,
        )
        answer = response["choices"][0]["message"].get("content", "")
        passed = cancelled and baseline_answer.strip() == "READY" and answer.strip() == "READY"
        report = {
            "passed": passed,
            "model": model,
            "baseline_answer": baseline_answer,
            "baseline_passed": "READY" in baseline_answer,
            "stream_chunks_before_cancel": chunks,
            "cancelled": cancelled,
            "successor_answer": answer,
        }
        root = Path(".acceptance") / str(uuid4())
        root.mkdir(parents=True)
        (root / "cancellation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({**report, "evidence": str(root)}, indent=2))
        if not passed:
            raise RuntimeError("Live cancellation diagnostic failed.")
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if instance is not None:
            await provider.unload_model(instance)
        await provider.close()


if __name__ == "__main__":
    asyncio.run(main())
