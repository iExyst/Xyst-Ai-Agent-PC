"""Real local specialist quota: block one assignment and its dependent before tools."""

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.states import RunState
from xaa.runtime.task_graph import TaskDefinition
from xaa.storage.runs import RunStore
from xaa.storage.tasks import TaskStore


async def main() -> None:
    root = (Path(".acceptance") / str(uuid4())).resolve()
    root.mkdir(parents=True)
    fixture = root / "input.txt"
    fixture.write_bytes(b"unchanged specialist quota fixture\n")
    provider = LMStudioProvider()
    model = "qwen/qwen3.5-9b"
    instance = None
    report: dict[str, Any] = {
        "passed": False,
        "model": model,
        "task_threshold": 1,
        "task_output_cap": 256,
    }
    try:
        await provider.models()
        instance = await provider.load_model(model, context_length=16384)
        store = RunStore(root / "runs.sqlite3")
        run = store.create_run(
            objective="Inspect input.txt and review it",
            acceptance_criteria=[],
            workspace=str(root),
            mode="observe",
            model=model,
            max_iterations=4,
            max_seconds=60,
            max_total_tokens=10000,
            max_output_tokens=512,
        )
        token = store.acquire_lease(run["id"], "diagnostic", seconds=180)
        assert token is not None
        tasks = TaskStore(store)
        tasks.save_plan(
            run["id"],
            token,
            [
                TaskDefinition(
                    id="inspect",
                    objective="Read input.txt with workspace_read_file and report its exact contents.",
                    max_iterations=1,
                    max_seconds=20,
                    max_total_tokens=1,
                    max_output_tokens=256,
                ),
                TaskDefinition(
                    id="review",
                    objective="Review the inspection evidence",
                    dependencies=("inspect",),
                    max_iterations=1,
                    max_seconds=20,
                ),
            ],
        )
        store.transition(run["id"], RunState.PLANNING, owner=token)
        store.transition(run["id"], RunState.RUNNING, owner=token)
        await Coordinator(store, provider).continue_plan(run["id"], token)
        rows = {row["local_id"]: row for row in tasks.records(run["id"])}
        events = store.list_events(run["id"])
        replies = [e for e in events if e["event_type"] == "task.model.message"]
        ready, _blocked = tasks.readiness(run["id"])
        report.update(
            run_id=run["id"],
            task_states={key: row["status"] for key, row in rows.items()},
            blocked_dependencies=[
                e["payload"]["task_id"]
                for e in events
                if e["event_type"] == "task.blocked"
                and e["payload"].get("reason") == "dependency_failed"
            ],
            task_reported_usage=replies[0]["payload"]["usage"] if replies else None,
            tool_intents=sum(e["event_type"] == "tool.intended" for e in events),
            fixture_unchanged=fixture.read_bytes() == b"unchanged specialist quota fixture\n",
            plan_source="explicit seeded assignment; real specialist inference",
        )
        report["passed"] = (
            rows["inspect"]["status"] == "blocked"
            and rows["review"]["status"] == "blocked"
            and ready == []
            and report["blocked_dependencies"] == ["review"]
            and not any(
                e["event_type"] == "task.inference.started"
                and e["payload"].get("task_id") == "review"
                for e in events
            )
            and len(replies) == 1
            and replies[0]["payload"]["usage"]["total_tokens"] > 1
            and replies[0]["payload"]["usage"]["completion_tokens"] <= 256
            and report["tool_intents"] == 0
            and report["fixture_unchanged"]
        )
        if not report["passed"]:
            raise RuntimeError("Specialist quota acceptance failed.")
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
