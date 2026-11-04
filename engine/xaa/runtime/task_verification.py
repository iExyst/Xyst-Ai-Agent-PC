"""Inspect specialist file outputs independently of their claimed results."""

import json

from xaa.runtime.executor import WorkspaceExecutor
from xaa.runtime.verification import FileContentCheck, verify_files
from xaa.storage.runs import utc_now
from xaa.storage.tasks import TaskStore


def inspect_task_files(
    tasks: TaskStore,
    executor: WorkspaceExecutor,
    run_id: str,
    parent_token: str,
    task_id: str,
    checks: list[FileContentCheck],
) -> list[str]:
    """Record actual file evidence; natural-language criteria still need review."""
    if not checks:
        raise ValueError("At least one explicit file requirement is needed.")
    record = next((row for row in tasks.records(run_id) if row["local_id"] == task_id), None)
    if record is None or record["status"] != "verifying":
        raise ValueError("Only a submitted candidate can be inspected.")
    failures = verify_files(executor, run_id, parent_token, checks)
    evidence = {
        "checks": [check.model_dump() for check in checks],
        "failures": failures,
        "passed": not failures,
        "checked_at": utc_now(),
    }
    with tasks.runs.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        tasks.runs.require_lease(connection, run_id, parent_token)
        changed = connection.execute(
            "UPDATE tasks SET checkpoint=?, updated_at=? WHERE id=? AND run_id=? AND status='verifying'",
            (json.dumps(evidence), utc_now(), f"{run_id}:{task_id}", run_id),
        ).rowcount
        if changed != 1:
            raise ValueError("Candidate is no longer awaiting verification.")
        tasks.runs.record_event(
            connection,
            run_id,
            "task.verification",
            {
                "task_id": task_id,
                "passed": not failures,
                "check_count": len(checks),
            },
        )
    return failures
