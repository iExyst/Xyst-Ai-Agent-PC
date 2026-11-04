"""Measure actual local-server output caps and fail-closed coordinator truncation."""

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.storage.runs import RunStore


async def main() -> None:
    root = (Path(".acceptance") / str(uuid4())).resolve()
    root.mkdir(parents=True)
    provider = LMStudioProvider()
    model = "qwen/qwen3.5-9b"
    instance = None
    report: dict[str, Any] = {"passed": False, "model": model, "max_output_tokens": 1}
    try:
        await provider.models()
        instance = await provider.load_model(model, context_length=16384)
        messages = [
            {"role": "user", "content": "Write a detailed explanation of the solar system."}
        ]
        response = await provider.chat(model=model, messages=messages, max_output_tokens=1)
        streamed_usage = None
        finishes: list[str] = []
        async for raw in provider.stream(model=model, messages=messages, max_output_tokens=1):
            chunk = json.loads(raw)
            if chunk.get("usage") is not None:
                streamed_usage = chunk["usage"]
            for choice in chunk.get("choices", []):
                if choice.get("finish_reason") is not None:
                    finishes.append(choice["finish_reason"])
        provider.verify_output_limit({"usage": streamed_usage}, 1)
        store = RunStore(root / "runs.sqlite3")
        run = store.create_run(
            objective="Read input.txt using workspace_read_file.",
            acceptance_criteria=[],
            workspace=str(root),
            mode="observe",
            model=model,
            max_output_tokens=1,
            max_seconds=60,
        )
        fixture = root / "input.txt"
        fixture.write_bytes(b"unchanged")
        await Coordinator(store, provider).execute(run["id"])
        events = store.list_events(run["id"])
        saved = store.get_run(run["id"])
        assert saved is not None
        report.update(
            nonstream_usage=response.get("usage"),
            stream_usage=streamed_usage,
            nonstream_finish=response["choices"][0].get("finish_reason"),
            stream_finishes=finishes,
            run_id=run["id"],
            run_state=saved["state"],
            tool_intents=sum(e["event_type"] == "tool.intended" for e in events),
        )
        report["passed"] = (
            response["usage"]["completion_tokens"] <= 1
            and report["nonstream_finish"] == "length"
            and "length" in finishes
            and saved["state"] == "blocked"
            and report["tool_intents"] == 0
            and fixture.read_bytes() == b"unchanged"
        )
        if not report["passed"]:
            raise RuntimeError("Output-cap acceptance failed.")
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
