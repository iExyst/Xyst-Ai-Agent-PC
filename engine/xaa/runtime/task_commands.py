"""Restricted command validation of a specialist's private snapshot, never its parent tree."""

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from xaa.runtime.command_contracts import CommandArguments
from xaa.runtime.command_controller import CommandController
from xaa.runtime.file_handles import open_for_read, opened_file_path
from xaa.runtime.workspace_snapshot import SnapshotFile, WorkspaceSnapshot
from xaa.runtime.wsl_transport import WSLTransport
from xaa.storage.commands import CommandJournal, command_artifact_root
from xaa.storage.runs import RunStore, TaskReadScope
from xaa.storage.task_workspaces import TaskWorkspaces


def command_evidence(store: RunStore, run_id: str, task_id: str) -> list[dict[str, Any]]:
    """Expose only task-owned durable outcomes with intact output and exact snapshot identity."""
    with store.connection() as connection:
        rows = connection.execute(
            "SELECT provider_call_id,arguments,result FROM tool_calls WHERE run_id=? AND task_id=? AND tool_name='workspace.run_command' AND tool_version='1' AND state='completed' ORDER BY created_at DESC LIMIT 16",
            (run_id, f"{run_id}:{task_id}"),
        ).fetchall()
        if not rows:
            return []
        base, files = TaskWorkspaces(store).view(connection, run_id, task_id)
        current = WorkspaceSnapshot(
            tuple(SnapshotFile(name, data) for name, data in files.items()),
            base.directories,
            base.excluded,
        ).sha256
    root = command_artifact_root(store)
    evidence: list[dict[str, Any]] = []
    for row in reversed(rows):
        result = json.loads(row["result"])
        intent = json.loads(row["arguments"])
        for stream in ("stdout", "stderr"):
            artifact = store.get_artifact(result[stream + "_artifact_id"])
            if (
                artifact is None
                or artifact["run_id"] != run_id
                or Path(artifact["storage_key"]).name != artifact["storage_key"]
            ):
                raise ValueError("Task command output artifact is unavailable.")
            with open_for_read(root / artifact["storage_key"]) as source:
                if not opened_file_path(source).is_relative_to(root):
                    raise PermissionError("Task output escaped managed storage.")
                data = source.read(1048577)
            if (
                len(data) != result[stream + "_bytes"]
                or hashlib.sha256(data).hexdigest() != result[stream + "_sha256"]
            ):
                raise ValueError("Task validation output changed after execution.")
        evidence.append(
            {
                "call_id": row["provider_call_id"],
                "argv": intent["argv"],
                "snapshot_sha256": result["snapshot_sha256"],
                "matches_current_private_tree": current == result["snapshot_sha256"],
                **{
                    key: result[key]
                    for key in (
                        "succeeded",
                        "exit_code",
                        "termination_reason",
                        "stdout_excerpt",
                        "stderr_excerpt",
                        "stdout_artifact_id",
                        "stderr_artifact_id",
                    )
                },
            }
        )
    return evidence


async def execute_task_command(
    store: RunStore,
    scope: TaskReadScope,
    call_id: str,
    arguments: dict[str, Any],
    *,
    deadline: float,
) -> dict[str, Any]:
    parsed = CommandArguments.model_validate(arguments)
    if deadline <= time.monotonic():
        raise TimeoutError("Specialist time allowance expired before command discovery.")
    journal = CommandJournal(store, task_scope=scope)
    with store.connection() as connection:
        journal.require_task(connection, scope.run_id, scope.parent_token)
    grant = journal.grant(scope.run_id, owner=scope.parent_token)
    if parsed.max_seconds > grant.max_seconds:
        raise PermissionError("Command exceeds the saved local-user grant.")
    transport = await WSLTransport.discover(grant.distribution, deadline=deadline)
    if transport.profile_sha256 != grant.profile_sha256:
        raise PermissionError("Execution profile changed after authorization.")
    return await CommandController(store, transport, task_scope=scope).execute(
        scope.run_id,
        scope.parent_token,
        call_id,
        parsed.model_dump(),
        idempotency_key=call_id,
        deadline=deadline,
    )
