"""Verify a real LM Studio coordinator run against a fresh fixture-file fact."""

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.storage.runs import RunStore


async def main() -> None:
    workspace = Path(".acceptance") / str(uuid4())
    workspace.mkdir(parents=True)
    expected = f"XAA-{uuid4().hex[:12]}"
    (workspace / "evidence.txt").write_text(f"Verification code: {expected}\n", encoding="utf-8")
    store = RunStore(workspace / "run.sqlite3")
    provider = LMStudioProvider(os.environ.get("LM_STUDIO_URL", "http://127.0.0.1:1234"))
    model = os.environ.get("LM_STUDIO_MODEL", "qwen3-0.6b")
    instance = None
    try:
        instance = await provider.load_model(model)
        run = store.create_run(
            objective="Call workspace_read_file with path evidence.txt, then report the exact verification code from that file.",
            acceptance_criteria=[
                "Read evidence.txt with the workspace tool and report its exact code."
            ],
            workspace=str(workspace.resolve()),
            mode="observe",
            model=model,
        )
        await Coordinator(store, provider).execute(run["id"])
        events = store.list_events(run["id"])
        messages = [
            event["payload"]["message"]
            for event in events
            if event["event_type"] == "model.message"
        ]
        final = messages[-1].get("content", "")
        completed_tools = [event for event in events if event["event_type"] == "tool.completed"]
        saved = store.get_run(run["id"])
        passed = bool(
            saved and saved["state"] == "verifying" and completed_tools and expected in final
        )
        report = {
            "passed": passed,
            "model": model,
            "run_id": run["id"],
            "expected_code": expected,
            "candidate_answer": final,
            "completed_tool_calls": len(completed_tools),
            "run_state": saved["state"] if saved else None,
            "database": str(store.path.resolve()),
        }
        (workspace / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        if not passed:
            raise RuntimeError("Live coordinator diagnostic failed; inspect the saved events.")
    finally:
        if instance is not None:
            await provider.unload_model(instance)
        await provider.close()


if __name__ == "__main__":
    asyncio.run(main())
