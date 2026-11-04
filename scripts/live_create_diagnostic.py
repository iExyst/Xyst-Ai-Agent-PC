"""Real-model exclusive file creation against a fresh isolated fixture."""

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.storage.runs import RunStore


async def main() -> None:
    root = (Path(".acceptance") / str(uuid4())).resolve()
    root.mkdir(parents=True)
    workspace = root / "fixture"
    workspace.mkdir()
    (workspace / "neighbor.txt").write_bytes(b"preserve")
    filename = f"created-{uuid4().hex[:8]}.txt"
    expected = f"created-code-{uuid4().hex}"
    store = RunStore(root / "runs.sqlite3")
    model = os.environ.get("LM_STUDIO_MODEL", "qwen/qwen3.5-9b")
    provider = LMStudioProvider(os.environ.get("LM_STUDIO_URL", "http://127.0.0.1:1234"))
    instance = None
    run = store.create_run(
        objective=f"Use workspace_create_file to create the NEW file {filename} with exact content {expected}. Do not add any newline. Then read the new file using workspace_read_file and report the code. Preserve neighbor.txt.",
        acceptance_criteria=["New file has exact requested bytes", "Neighbor is preserved"],
        workspace=str(workspace),
        mode="workspace",
        model=model,
        file_checks=[{"path": filename, "expected_content": expected}],
    )
    try:
        instance = await provider.load_model(model, context_length=16384)
        await Coordinator(store, provider).execute(run["id"])
        events = store.list_events(run["id"])
        calls = [
            call
            for event in events
            if event["event_type"] == "model.message"
            for call in event["payload"].get("message", {}).get("tool_calls", [])
        ]
        creates = [call for call in calls if call["function"]["name"] == "workspace_create_file"]
        reads = [call for call in calls if call["function"]["name"] == "workspace_read_file"]
        target = workspace / filename
        exact = target.exists() and target.read_bytes() == expected.encode()
        preserved = (workspace / "neighbor.txt").read_bytes() == b"preserve"
        saved = store.get_run(run["id"])
        passed = bool(
            saved
            and saved["state"] == "verifying"
            and exact
            and preserved
            and len(creates) == 1
            and reads
            and not store.unresolved_tool_calls(run["id"])
        )
        report = {
            "passed": passed,
            "model": model,
            "run_id": run["id"],
            "create_proposals": len(creates),
            "read_proposals": len(reads),
            "exact_file_bytes": exact,
            "neighbor_preserved": preserved,
            "state": saved["state"] if saved else None,
        }
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({**report, "evidence": str(root)}, indent=2))
        if not passed:
            raise RuntimeError("Live creation acceptance failed.")
    finally:
        if instance is not None:
            await provider.unload_model(instance)
        await provider.close()


if __name__ == "__main__":
    asyncio.run(main())
