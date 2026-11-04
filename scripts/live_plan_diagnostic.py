"""Exercise real local-model directory discovery and durable plan creation."""

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.storage.runs import RunStore
from xaa.storage.tasks import TaskStore


async def main() -> None:
    root = (Path(".acceptance") / str(uuid4())).resolve()
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    filename = f"project-{uuid4().hex[:12]}.txt"
    (workspace / filename).write_bytes(b"planning fixture\n")
    store = RunStore(root / "plan.sqlite3")
    model = os.environ.get("LM_STUDIO_MODEL", "qwen/qwen3.5-9b")
    provider = LMStudioProvider(os.environ.get("LM_STUDIO_URL", "http://127.0.0.1:1234"))
    instance = None
    report: dict[str, object] = {"passed": False, "model": model, "evidence": str(root)}
    try:
        instance = await provider.load_model(model, context_length=16384)
        run = store.create_run(
            objective="First list the workspace root using workspace_list_directory. Then use plan_save to save exactly two tasks: inspect (researcher, no dependencies) and review (reviewer, depends on inspect). Include the actual discovered filename in the inspect objective. Give each task max_iterations 2 and max_seconds 60, permission workspace.read, and explicit acceptance_criteria. Do not read or modify files and do not execute the tasks. Finish by saying the plan is saved and execution is pending.",
            acceptance_criteria=["Discover the real filename and save a dependency plan."],
            workspace=str(workspace),
            mode="observe",
            model=model,
        )
        await Coordinator(store, provider).execute(run["id"])
        tasks = TaskStore(store)
        records = tasks.records(run["id"])
        calls = [
            call["function"]["name"]
            for event in store.list_events(run["id"])
            if event["event_type"] == "model.message"
            for call in event["payload"]["message"].get("tool_calls", [])
        ]
        by_id = {row["local_id"]: row for row in records}
        saved = store.get_run(run["id"])
        passed = (
            saved is not None
            and saved["state"] == "verifying"
            and set(by_id) == {"inspect", "review"}
            and filename in by_id["inspect"]["objective"]
            and by_id["review"]["dependencies"] == ["inspect"]
            and tasks.readiness(run["id"]) == (["inspect"], [])
            and all(row["status"] == "queued" for row in records)
            and calls == ["workspace_list_directory", "plan_save"]
            and not store.unresolved_tool_calls(run["id"])
            and (workspace / filename).read_bytes() == b"planning fixture\n"
        )
        report.update(
            passed=passed,
            run_id=run["id"],
            state=saved["state"] if saved else None,
            filename=filename,
            tool_calls=calls,
            tasks=records,
        )
        if not passed:
            raise RuntimeError("Real-model plan diagnostic failed.")
    except Exception as exc:
        report["error"] = type(exc).__name__
        raise
    finally:
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({key: value for key, value in report.items() if key != "tasks"}, indent=2))
        try:
            if instance is not None:
                await provider.unload_model(instance)
        finally:
            await provider.close()


if __name__ == "__main__":
    asyncio.run(main())
