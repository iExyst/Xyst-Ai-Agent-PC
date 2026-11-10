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


def verify_command_output(store: RunStore, run_id: str, result: dict[str, Any]) -> None:
    root = command_artifact_root(store)
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
    evidence: list[dict[str, Any]] = []
    for row in reversed(rows):
        result = json.loads(row["result"])
        intent = json.loads(row["arguments"])
        verify_command_output(store, run_id, result)
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


def read_dependency_command_output(
    store: RunStore,
    scope: TaskReadScope,
    dependency_id: str,
    artifact_id: str,
    *,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    """Read a bounded byte range from validation output of a reviewed dependency."""
    if offset < 0 or not 1 <= limit <= 16384:
        raise ValueError("Reviewer output reads require a valid offset and a 16 KiB maximum.")
    with store.connection() as connection:
        store.require_lease(connection, scope.run_id, scope.parent_token)
        store.require_task_read_scope(connection, scope.run_id, scope.task_id, scope.task_token)
        relation = connection.execute(
            "SELECT reviewer.assigned_role,dependency.status FROM tasks AS reviewer "
            "JOIN task_dependencies AS edge ON edge.task_id=reviewer.id "
            "JOIN tasks AS dependency ON dependency.id=edge.depends_on_task_id "
            "WHERE reviewer.id=? AND reviewer.run_id=? AND dependency.id=?",
            (
                f"{scope.run_id}:{scope.task_id}",
                scope.run_id,
                f"{scope.run_id}:{dependency_id}",
            ),
        ).fetchone()
        if relation is None or relation["assigned_role"] != "reviewer":
            raise PermissionError("Only a reviewer may inspect a declared dependency artifact.")
        if relation["status"] != "completed":
            raise PermissionError("Validation output is available only after task review.")
    evidence = command_evidence(store, scope.run_id, dependency_id)
    if not any(
        artifact_id in {item["stdout_artifact_id"], item["stderr_artifact_id"]} for item in evidence
    ):
        raise PermissionError(
            "Output artifact is not part of this dependency's validation evidence."
        )
    artifact = store.get_artifact(artifact_id)
    root = command_artifact_root(store)
    if (
        artifact is None
        or artifact["run_id"] != scope.run_id
        or Path(artifact["storage_key"]).name != artifact["storage_key"]
        or not str(artifact["provenance"]).startswith("execution.")
    ):
        raise PermissionError("Validation output artifact is unavailable.")
    with open_for_read(root / artifact["storage_key"]) as source:
        if not opened_file_path(source).is_relative_to(root):
            raise PermissionError("Validation output escaped managed storage.")
        if source.seek(0, 2) != artifact["size_bytes"]:
            raise ValueError("Validation output size changed after execution.")
        source.seek(offset)
        data = source.read(limit)
    return {
        "dependency_id": dependency_id,
        "artifact_id": artifact_id,
        "offset": offset,
        "total_bytes": artifact["size_bytes"],
        "bytes_read": len(data),
        "sha256": artifact["sha256"],
        "text": data.decode("utf-8", errors="replace"),
        "trust": "untrusted_test_output",
        "eof": offset + len(data) >= artifact["size_bytes"],
    }


def read_task_input_artifact(
    store: RunStore,
    scope: TaskReadScope,
    artifact_id: str,
    *,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    """Read a bounded character page from an artifact explicitly assigned to this task."""
    if offset < 0 or not 1 <= limit <= 16384:
        raise ValueError("Task input reads require a valid offset and a 16 KiB maximum.")
    with store.connection() as connection:
        store.require_lease(connection, scope.run_id, scope.parent_token)
        store.require_task_read_scope(connection, scope.run_id, scope.task_id, scope.task_token)
        row = connection.execute(
            "SELECT a.* FROM artifacts a JOIN tasks t ON t.run_id=a.run_id "
            "WHERE a.id=? AND a.run_id=? AND a.task_id IS NULL AND t.id=?",
            (artifact_id, scope.run_id, f"{scope.run_id}:{scope.task_id}"),
        ).fetchone()
        task_row = connection.execute(
            "SELECT input_artifacts FROM tasks WHERE id=? AND run_id=?",
            (f"{scope.run_id}:{scope.task_id}", scope.run_id),
        ).fetchone()
    if (
        row is None
        or task_row is None
        or artifact_id not in json.loads(task_row["input_artifacts"])
    ):
        raise PermissionError("Artifact is not assigned as input to this task.")
    if row["media_type"] != "text/plain" or row["size_bytes"] > 4 * 1024 * 1024:
        raise ValueError("Task input must be a supported plain-text artifact no larger than 4 MiB.")
    root = (store.path.parent / "artifacts").resolve(strict=True)
    if Path(row["storage_key"]).name != row["storage_key"]:
        raise PermissionError("Task input artifact path is invalid.")
    with open_for_read(root / row["storage_key"]) as source:
        if not opened_file_path(source).is_relative_to(root):
            raise PermissionError("Task input artifact escaped managed storage.")
        data = source.read(4 * 1024 * 1024 + 1)
    if len(data) != row["size_bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise ValueError("Task input artifact changed after it was saved.")
    content = data.decode("utf-8", errors="replace")
    page = content[offset : offset + limit]
    return {
        "artifact_id": artifact_id,
        "offset": offset,
        "total_characters": len(content),
        "characters_read": len(page),
        "sha256": row["sha256"],
        "content": page,
        "trust": "untrusted_user_artifact",
        "eof": offset + len(page) >= len(content),
    }


async def execute_task_command(
    store: RunStore,
    scope: TaskReadScope,
    call_id: str,
    arguments: dict[str, Any],
    *,
    deadline: float,
    model_call_id: str,
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
        task_call_id=model_call_id,
    )
