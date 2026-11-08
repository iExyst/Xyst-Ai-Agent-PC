"""Verify a real model-generated plan through dependent specialist dispatch."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.states import RunState
from xaa.runtime.task_review import TaskReviewDecision, candidate_evidence, review_candidate
from xaa.storage.runs import RunStore
from xaa.storage.tasks import TaskStore


async def main() -> None:
    root = (Path(".acceptance") / str(uuid4())).resolve()
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    filename = f"review-target-{uuid4().hex[:12]}.txt"
    expected = f"XAA-{uuid4().hex}"
    (workspace / filename).write_text(expected, encoding="utf-8")
    store = RunStore(root / "plan-dispatch.sqlite3")
    model = os.environ.get("LM_STUDIO_MODEL", "qwen/qwen3.5-9b")
    provider = LMStudioProvider(os.environ.get("LM_STUDIO_URL", "http://127.0.0.1:1234"))
    instance = None
    report: dict[str, object] = {
        "passed": False,
        "model": model,
        "evidence": str(root),
        "fixture_token": expected,
        "final_review_left_pending": True,
    }
    try:
        instance = await provider.load_model(model, context_length=16384)
        run = store.create_run(
            objective=(
                f"First list the workspace root using workspace_list_directory. Then use plan_save "
                f"to save exactly two tasks: name the researcher task inspect (no dependencies) "
                f"and name the reviewer task review (depends on inspect). Include the actual "
                f"discovered filename {filename} "
                f"in the inspect objective and require the researcher to read it and return its "
                f"exact text. Require the reviewer to independently read the same file and compare "
                f"it with inspect's candidate. Give each task max_iterations 4, max_seconds 150, "
                f"permission workspace.read, and explicit acceptance_criteria. Do not read or "
                f"modify files and do not execute the tasks. Finish by saying the plan is saved "
                f"and execution is pending."
            ),
            acceptance_criteria=["Save and execute an inspect then independent-review plan."],
            workspace=str(workspace),
            mode="observe",
            model=model,
            max_iterations=32,
            max_seconds=600,
        )
        run_id = run["id"]
        report["run_id"] = run_id
        coordinator = Coordinator(store, provider)
        await coordinator.execute(run_id)
        tasks = TaskStore(store)
        planned = tasks.records(run_id)
        calls = [
            call["function"]["name"]
            for event in store.list_events(run_id)
            if event["event_type"] == "model.message"
            for call in event["payload"]["message"].get("tool_calls", [])
        ]
        by_role = {item["assigned_role"]: item for item in planned}
        saved = store.get_run(run_id)
        if (
            saved is None
            or saved["state"] != RunState.VERIFYING
            or set(by_role) != {"researcher", "reviewer"}
            or len(planned) != 2
            or filename not in by_role["researcher"]["objective"]
            or by_role["reviewer"]["dependencies"] != [by_role["researcher"]["local_id"]]
            or tasks.readiness(run_id) != ([by_role["researcher"]["local_id"]], [])
            or not calls
            or calls[0] != "workspace_list_directory"
            or not set(calls[1:]) <= {"plan_save"}
            or "plan_save" not in calls[1:]
        ):
            raise RuntimeError("Model did not save the expected dependency graph.")

        # Exercise the same authenticated user-dispatch state path used by the API.
        parent = store.acquire_lease(run_id, "live-plan-dispatch", seconds=300)
        if parent is None:
            raise RuntimeError("Could not acquire the plan for user dispatch.")
        store.transition(
            run_id, RunState.RUNNING, reason="user_dispatched_ready_tasks", owner=parent
        )
        await coordinator.continue_plan(run_id, parent)

        inspect_id = by_role["researcher"]["local_id"]
        review_id = by_role["reviewer"]["local_id"]
        source = candidate_evidence(store, run_id, inspect_id)
        source_candidate = source.get("candidate")
        source_reads = [
            event
            for event in store.list_events(run_id)
            if event["event_type"] == "task.tool.result"
            and event["payload"].get("task_id") == inspect_id
            and event["payload"].get("result", {}).get("path") == filename
        ]
        source_read_results = [event["payload"]["result"] for event in source_reads]
        source_statuses = {item["local_id"]: item["status"] for item in tasks.records(run_id)}
        if (
            source_statuses != {inspect_id: "verifying", review_id: "queued"}
            or not isinstance(source_candidate, str)
            or expected not in source_candidate
            or not source_read_results
            or any(
                result.get("text") != expected or result.get("truncated") is not False
                for result in source_read_results
            )
            or source["status"] != "verifying"
        ):
            raise RuntimeError("The dispatched researcher did not produce exact read evidence.")

        evidence_ids = [item["call_id"] for item in source["evidence"]]
        source_read_ids = {event["payload"]["journal_id"] for event in source_reads}
        if not evidence_ids or not set(evidence_ids) <= source_read_ids:
            raise RuntimeError("Research candidate did not cite its saved file-read evidence.")
        await review_candidate(
            store,
            run_id,
            inspect_id,
            TaskReviewDecision(
                decision="accept",
                rationale="Harness compared the candidate byte-for-byte with saved file-read evidence.",
                confirmed_criteria=list(range(len(source["acceptance_criteria"]))),
                evidence_call_ids=evidence_ids,
            ),
        )

        parent = store.acquire_lease(run_id, "live-plan-review-dispatch", seconds=300)
        if parent is None:
            raise RuntimeError("Could not reacquire the run for dependent review dispatch.")
        store.transition(
            run_id, RunState.RUNNING, reason="user_dispatched_ready_tasks", owner=parent
        )
        await coordinator.continue_plan(run_id, parent)

        reviewer = candidate_evidence(store, run_id, review_id)
        review_reads = [
            event
            for event in store.list_events(run_id)
            if event["event_type"] == "task.tool.result"
            and event["payload"].get("task_id") == review_id
            and event["payload"].get("result", {}).get("path") == filename
        ]
        final_records = tasks.records(run_id)
        final_by_id = {item["local_id"]: item for item in final_records}
        final_run = store.get_run(run_id)
        reviewer_candidate = reviewer.get("candidate")
        passed = (
            final_run is not None
            and final_run["state"] == RunState.VERIFYING
            and final_by_id[inspect_id]["status"] == "completed"
            and final_by_id[review_id]["status"] == "verifying"
            and reviewer["status"] == "verifying"
            and isinstance(reviewer_candidate, str)
            and expected in reviewer_candidate
            and bool(review_reads)
            and filename in [item["path"] for item in reviewer["evidence"]]
            and not store.unresolved_tool_calls(run_id)
            and (workspace / filename).read_text(encoding="utf-8") == expected
        )
        report.update(
            passed=passed,
            run_id=run_id,
            plan_tool_calls=calls,
            plan=[
                {"id": item["local_id"], "dependencies": item["dependencies"]} for item in planned
            ],
            inspect_candidate=source_candidate,
            inspect_read_count=len(source_reads),
            inspect_evidence_count=len(evidence_ids),
            inspect_user_review="accepted against persisted read evidence",
            review_candidate=reviewer_candidate,
            review_read_count=len(review_reads),
            final_task_statuses={key: value["status"] for key, value in final_by_id.items()},
            final_review_left_pending=(final_by_id[review_id]["status"] == "verifying"),
            unresolved_tool_calls=len(store.unresolved_tool_calls(run_id)),
            token_usage=store.token_usage(run_id),
        )
        if not passed:
            raise RuntimeError("Model-generated dependent specialist acceptance failed.")
    except Exception as exc:
        report["error"] = type(exc).__name__
        if "run_id" in report:
            run_id = str(report["run_id"])
            saved = store.get_run(run_id)
            report["failed_state"] = saved["state"] if saved else None
            report["state_changes"] = [
                event["payload"]
                for event in store.list_events(run_id)
                if event["event_type"] == "run.state_changed"
            ]
            report["task_statuses"] = {
                item["local_id"]: item["status"] for item in TaskStore(store).records(run_id)
            }
            report["model_tool_calls"] = [
                call.get("function", {}).get("name")
                for event in store.list_events(run_id)
                if event["event_type"] == "model.message"
                for call in event["payload"].get("message", {}).get("tool_calls", [])
            ]
            report["model_failure_codes"] = [
                event["payload"].get("error_code")
                for event in store.list_events(run_id)
                if event["event_type"] in {"model.stream_rejected", "model.incomplete"}
            ]
        raise
    finally:
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        try:
            if instance is not None:
                await provider.unload_model(instance)
        finally:
            await provider.close()


if __name__ == "__main__":
    asyncio.run(main())
