"""Verify a real local model independently inspects a specialist's file evidence."""

import asyncio
import json
import os
import time
from pathlib import Path
from uuid import uuid4

from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.specialists import SpecialistRunner
from xaa.runtime.states import RunState
from xaa.runtime.task_graph import TaskDefinition
from xaa.runtime.task_review import TaskReviewDecision, candidate_evidence, review_candidate
from xaa.storage.runs import RunStore
from xaa.storage.tasks import TaskStore


async def main() -> None:
    root = (Path(".acceptance") / str(uuid4())).resolve()
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    token = f"XAA-{uuid4().hex}"
    (workspace / "review-target.txt").write_text(token, encoding="utf-8")
    store = RunStore(root / "review.sqlite3")
    model = os.environ.get("LM_STUDIO_MODEL", "qwen/qwen3.5-9b")
    run_id = store.create_run(
        objective="Independently verify a specialist's statement against the file.",
        acceptance_criteria=[],
        workspace=str(workspace),
        mode="observe",
        model=model,
        max_iterations=32,
    )["id"]
    parent = store.acquire_lease(run_id, "live-review-acceptance", seconds=300)
    assert parent is not None
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        parent,
        [
            TaskDefinition(
                id="research",
                objective=(
                    "Call workspace_read_file exactly once on review-target.txt. Return the exact "
                    "text from the tool result as your candidate. Do not guess or reread."
                ),
                acceptance_criteria=("Candidate contains the exact file text.",),
                role="researcher",
                max_iterations=4,
                max_seconds=120,
            ),
            TaskDefinition(
                id="review",
                objective=(
                    "Independently inspect review-target.txt with workspace_read_file. Compare "
                    "the returned bytes with the research dependency's candidate, and report the "
                    "exact token and whether it matches. Do not rely on the candidate summary."
                ),
                acceptance_criteria=(
                    "Reviewer personally rereads the file and accurately reports whether the candidate matches.",
                ),
                dependencies=("research",),
                role="reviewer",
                max_iterations=8,
                max_seconds=180,
            ),
        ],
    )
    store.transition(run_id, RunState.PLANNING, owner=parent)
    store.transition(run_id, RunState.RUNNING, owner=parent)
    provider = LMStudioProvider(os.environ.get("LM_STUDIO_URL", "http://127.0.0.1:1234"))
    report: dict[str, object] = {
        "passed": False,
        "evidence": str(root),
        "model": model,
        "harness_review_decisions_automated": True,
    }
    try:
        # Use the model already loaded by the user's LM Studio session. This
        # diagnostic must not change model lifecycle or request a download.
        await provider.diagnose_tools(model)
        runner = SpecialistRunner(store, provider)
        deadline = time.monotonic() + 240
        first = await runner.run_ready(run_id, parent, deadline=deadline)
        source = candidate_evidence(store, run_id, "research")
        first_statuses = {item["task_id"]: item["status"] for item in first}
        if (
            first_statuses != {"research": "verifying", "review": "queued"}
            or token not in str(source["candidate"])
            or not source["evidence"]
        ):
            raise RuntimeError("Research specialist did not produce evidence-backed candidate.")

        store.transition(run_id, RunState.VERIFYING, owner=parent)
        store.release_lease(run_id, parent)
        await review_candidate(
            store,
            run_id,
            "research",
            TaskReviewDecision(
                decision="accept",
                rationale="Acceptance harness matched the random token to the persisted file-read evidence.",
                confirmed_criteria=[0],
                evidence_call_ids=[source["evidence"][0]["call_id"]],
            ),
        )
        parent = store.acquire_lease(run_id, "live-review-acceptance", seconds=300)
        assert parent is not None
        store.transition(run_id, RunState.RUNNING, owner=parent)
        second = await runner.run_ready(run_id, parent, deadline=deadline)
        reviewer = candidate_evidence(store, run_id, "review")
        review_events = [
            event
            for event in store.list_events(run_id)
            if event["event_type"] == "task.tool.result"
            and event["payload"].get("task_id") == "review"
            and event["payload"].get("result", {}).get("path") == "review-target.txt"
        ]
        second_statuses = {item["task_id"]: item["status"] for item in second}
        reviewer_candidate = reviewer.get("candidate")
        reviewer_evidence_paths = [item["path"] for item in reviewer["evidence"]]
        final_run = store.get_run(run_id)
        passed = (
            second_statuses == {"research": "completed", "review": "verifying"}
            and isinstance(reviewer_candidate, str)
            and token in reviewer_candidate
            and bool(review_events)
            and "review-target.txt" in reviewer_evidence_paths
            and reviewer["status"] == "verifying"
            and final_run is not None
            and final_run["state"] == "running"
        )
        report.update(
            passed=passed,
            run_id=run_id,
            research_candidate=source["candidate"],
            reviewer_candidate=reviewer_candidate,
            reviewer_file_reads=len(review_events),
            reviewer_evidence_paths=reviewer_evidence_paths,
            reviewer_status=reviewer["status"],
            final_review_left_pending=True,
            token_usage=store.token_usage(run_id),
        )
        if not passed:
            raise RuntimeError("Independent real-model reviewer acceptance failed.")
    except Exception as exc:
        report["error"] = type(exc).__name__
        raise
    finally:
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        if parent is not None:
            store.release_lease(run_id, parent)
        await provider.close()


if __name__ == "__main__":
    asyncio.run(main())
