"""Explicit user review of specialist candidates against actual file evidence."""

import json
import time
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from xaa.runtime.task_commands import command_evidence
from xaa.runtime.task_integration import IntegrationResolution, integrate_changes
from xaa.runtime.tool_worker import execute_filesystem
from xaa.storage.runs import RunStore, utc_now
from xaa.storage.task_workspaces import TaskWorkspaces
from xaa.storage.tasks import TaskStore


class TaskReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decision: Literal["accept", "reject"]
    rationale: str = Field(min_length=1, max_length=4000)
    confirmed_criteria: list[int] = Field(default_factory=lambda: list[int](), max_length=50)
    evidence_call_ids: list[str] = Field(default_factory=list, max_length=16)
    changes_confirmed: bool = False
    validation_call_ids: list[str] = Field(default_factory=list, max_length=16)
    resolutions: list[IntegrationResolution] = Field(
        default_factory=lambda: list[IntegrationResolution](), max_length=16
    )


def candidate_evidence(store: RunStore, run_id: str, task_id: str) -> dict[str, Any]:
    task = next(
        (row for row in TaskStore(store).records(run_id) if row["local_id"] == task_id), None
    )
    if task is None:
        raise KeyError(task_id)
    evidence: list[dict[str, Any]] = []
    for event in store.list_events(run_id):
        payload = event["payload"]
        if event["event_type"] != "task.tool.result" or payload.get("task_id") != task_id:
            continue
        with store.connection() as connection:
            journal = connection.execute(
                "SELECT tool_name, result FROM tool_calls WHERE provider_call_id=? AND run_id=? AND state='completed'",
                (payload["journal_id"], run_id),
            ).fetchone()
        if journal is None or journal["tool_name"] != "workspace.read_file":
            continue
        result = json.loads(journal["result"])
        if result != payload["result"] or result.get("truncated") or not result.get("sha256"):
            continue
        evidence.append(
            {
                "call_id": payload["journal_id"],
                "path": result["path"],
                "sha256": result["sha256"],
                "excerpt": result["text"][:4096],
                "excerpt_truncated": len(result["text"]) > 4096,
            }
        )
    result: dict[str, Any] = json.loads(task["result"]) if task["result"] else {}
    return {
        "task_id": task_id,
        "status": task["status"],
        "candidate": result.get("candidate"),
        "acceptance_criteria": task["acceptance_criteria"],
        "evidence": evidence[-16:],
        "workspace_changes": TaskWorkspaces(store).changes(run_id, task_id),
        "validation_required": "workspace.command" in task["permission_scope"],
        "validation_evidence": command_evidence(store, run_id, task_id),
    }


async def review_candidate(
    store: RunStore, run_id: str, task_id: str, decision: TaskReviewDecision
) -> None:
    if not decision.rationale.strip():
        raise ValueError("Explain the review decision.")
    run = store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    if run["state"] != "verifying":
        raise ValueError("The parent run must be awaiting review.")
    token = store.acquire_lease(run_id, "local-user-review", seconds=180)
    if token is None:
        raise ValueError("The run is still owned by an active worker.")
    try:
        snapshot = candidate_evidence(store, run_id, task_id)
        if snapshot["status"] != "verifying":
            raise ValueError("Only a candidate awaiting review can be decided.")
        checked: list[dict[str, str]] = []
        if decision.decision == "accept":
            if snapshot["validation_required"]:
                validations = {item["call_id"]: item for item in snapshot["validation_evidence"]}
                selected = decision.validation_call_ids
                if (
                    not selected
                    or len(set(selected)) != len(selected)
                    or any(
                        call_id not in validations
                        or not validations[call_id]["succeeded"]
                        or not validations[call_id]["matches_current_private_tree"]
                        for call_id in selected
                    )
                ):
                    raise ValueError(
                        "Review successful validation evidence for the exact current private tree."
                    )
            if snapshot["workspace_changes"] and not decision.changes_confirmed:
                raise ValueError("Review the private changes and explicitly authorize integration.")
            store.resolve_interrupted_reads(run_id, token)
            deadline = time.monotonic() + 10
            criteria = snapshot["acceptance_criteria"]
            if not criteria or sorted(decision.confirmed_criteria) != list(range(len(criteria))):
                raise ValueError("Confirm every acceptance criterion exactly once.")
            evidence = {item["call_id"]: item for item in snapshot["evidence"]}
            if not decision.evidence_call_ids or len(set(decision.evidence_call_ids)) != len(
                decision.evidence_call_ids
            ):
                raise ValueError("Select actual file evidence for this task.")
            for call_id in decision.evidence_call_ids:
                if call_id not in evidence:
                    raise ValueError("Evidence does not belong to this task.")
                item = evidence[call_id]
                if snapshot["workspace_changes"]:
                    checked.append(
                        {"call_id": call_id, "path": item["path"], "sha256": item["sha256"]}
                    )
                    continue
                identity = f"review-{uuid4()}"
                current = await execute_filesystem(
                    store,
                    run_id,
                    token,
                    identity,
                    "read",
                    {"path": item["path"]},
                    idempotency_key=identity,
                    internal=True,
                    timeout=min(10, max(0, deadline - time.monotonic())),
                )
                if current["sha256"] != item["sha256"] or current["truncated"]:
                    raise ValueError("File evidence changed since the specialist read it.")
                checked.append({"call_id": call_id, "path": item["path"], "sha256": item["sha256"]})
        integrated: list[dict[str, Any]] = []
        if decision.decision == "accept" and snapshot["workspace_changes"]:
            integrated = await integrate_changes(
                store, run_id, task_id, token, decision.resolutions
            )
        state = "completed" if decision.decision == "accept" else "failed"
        with store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            store.require_lease(connection, run_id, token)
            if (
                connection.execute("SELECT state FROM runs WHERE id=?", (run_id,)).fetchone()[0]
                != "verifying"
            ):
                raise ValueError("The parent run is no longer awaiting review.")
            changed = connection.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE id=? AND run_id=? AND status='verifying'",
                (state, utc_now(), f"{run_id}:{task_id}", run_id),
            ).rowcount
            if changed != 1:
                raise ValueError("The candidate was already decided.")
            store.record_event(
                connection,
                run_id,
                "task.reviewed",
                {
                    "task_id": task_id,
                    "actor": "local-user",
                    **decision.model_dump(),
                    "checked_evidence": checked,
                    "integrated_changes": integrated,
                    "state": state,
                },
            )
    finally:
        store.release_lease(run_id, token)
