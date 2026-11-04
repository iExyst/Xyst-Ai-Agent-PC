"""Real local-model reported-token threshold acceptance, with no workspace effects."""

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
    fixture = root / "input.txt"
    fixture.write_bytes(b"preserve threshold fixture\n")
    stamp = fixture.stat().st_mtime_ns
    store = RunStore(root / "runs.sqlite3")
    provider = LMStudioProvider("http://127.0.0.1:1234")
    model = "qwen/qwen3.5-9b"
    instance = None
    report: dict[str, Any] = {
        "passed": False,
        "threshold": 1,
        "model": model,
        "scope": "reported run-tree tokens; including run-triggered diagnostics; not an inference ceiling",
    }
    try:
        await provider.models()
        instance = await provider.load_model(model, context_length=16384)
        run = store.create_run(
            objective="Use workspace_read_file to read input.txt and report its exact contents.",
            acceptance_criteria=[],
            workspace=str(root),
            mode="observe",
            model=model,
            max_total_tokens=1,
            max_iterations=4,
            max_seconds=60,
        )
        await Coordinator(store, provider).execute(run["id"])
        events = store.list_events(run["id"])
        messages = [e for e in events if e["event_type"] == "model.message"]
        usage = store.token_usage(run["id"])
        proposed = [
            c["function"]["name"]
            for e in messages
            for c in e["payload"]["message"].get("tool_calls", [])
        ]
        saved = store.get_run(run["id"])
        assert saved is not None
        passed = (
            saved["state"] == "blocked"
            and usage["total_tokens"] > 1
            and not proposed
            and any(e["event_type"] == "diagnostic.inference.completed" for e in events)
            and not any(e["event_type"] == "tool.intended" for e in events)
            and fixture.read_bytes() == b"preserve threshold fixture\n"
            and fixture.stat().st_mtime_ns == stamp
        )
        report.update(
            passed=passed,
            run_id=run["id"],
            usage=usage,
            proposed_tools=proposed,
            tool_intents=0 if passed else None,
        )
        if not passed:
            raise RuntimeError("Reported-token threshold acceptance failed.")
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
