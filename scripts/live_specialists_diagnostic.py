"""Verify two real local specialists retrieve independent file evidence."""

import asyncio
import json
import os
import time
from pathlib import Path
from uuid import uuid4

from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.specialists import SpecialistRunner
from xaa.runtime.task_graph import TaskDefinition
from xaa.storage.runs import RunStore
from xaa.storage.tasks import TaskStore


async def main() -> None:
    root = (Path(".acceptance") / str(uuid4())).resolve()
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    codes = {name: f"XAA-{uuid4().hex}" for name in ("alpha", "beta")}
    for name, code in codes.items():
        (workspace / f"{name}.txt").write_bytes(code.encode())
    store = RunStore(root / "specialists.sqlite3")
    model = os.environ.get("LM_STUDIO_MODEL", "qwen/qwen3.5-9b")
    streaming_requested = os.environ.get("XAA_STREAM_RESPONSES") == "1"
    run_id = store.create_run(
        objective="Inspect two independent files",
        acceptance_criteria=[],
        workspace=str(workspace),
        mode="observe",
        model=model,
    )["id"]
    parent = store.acquire_lease(run_id, "acceptance", seconds=180)
    assert parent is not None
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        parent,
        [
            TaskDefinition(
                id=name,
                objective=f"Use workspace_read_file exactly once on {name}.txt. After the result, return its full text as your final candidate answer. Do not call more tools or repeat the read. Do not invent the code.",
                acceptance_criteria=("Candidate contains the code read from the actual file.",),
                role="researcher",
                max_iterations=3,
                max_seconds=120,
            )
            for name in codes
        ],
    )
    provider = LMStudioProvider(os.environ.get("LM_STUDIO_URL", "http://127.0.0.1:1234"))
    instance = None
    report: dict[str, object] = {"passed": False, "evidence": str(root), "model": model}
    try:
        instance = await provider.load_model(model, context_length=16384)
        await provider.diagnose_tools(model)
        statuses = await SpecialistRunner(
            store, provider, stream_responses=streaming_requested
        ).run_ready(run_id, parent, deadline=time.monotonic() + 150)
        records = tasks.records(run_id)
        deltas = [
            event
            for event in store.list_events(run_id)
            if event["event_type"] == "task.model.delta"
        ]
        streamed_task_counts = {
            name: sum(event["payload"].get("task_id") == name for event in deltas) for name in codes
        }
        passed = all(
            row["status"] == "verifying"
            and codes[row["local_id"]] in json.loads(row["result"])["candidate"]
            for row in records
        )
        passed = (
            passed
            and len(records) == 2
            and not store.conversation_messages(run_id)
            and not store.unresolved_tool_calls(run_id)
            and (
                not streaming_requested
                or all(count > 0 for count in streamed_task_counts.values())
                and all(len(event["payload"].get("text", "")) <= 128 for event in deltas)
            )
        )
        report.update(
            passed=passed,
            run_id=run_id,
            statuses=statuses,
            streaming_requested=streaming_requested,
            streamed_task_event_counts=streamed_task_counts,
            max_delta_characters=max(
                (len(event["payload"].get("text", "")) for event in deltas), default=0
            ),
            results=[
                {"task_id": row["local_id"], "result": json.loads(row["result"])} for row in records
            ],
            usage=store.token_usage(run_id),
        )
        if not passed:
            raise RuntimeError("Real specialist acceptance failed.")
    except Exception as exc:
        report["error"] = type(exc).__name__
        raise
    finally:
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        store.release_lease(run_id, parent)
        try:
            if instance is not None:
                await provider.unload_model(instance)
        finally:
            await provider.close()


if __name__ == "__main__":
    asyncio.run(main())
