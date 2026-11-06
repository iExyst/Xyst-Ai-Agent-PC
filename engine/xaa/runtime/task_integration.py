"""Explicit user-reviewed integration of private specialist changes, with conflict choices and CAS."""

import hashlib
import json
import time
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from xaa.runtime.command_contracts import command_workspace
from xaa.runtime.executor import WorkspaceExecutor
from xaa.runtime.tool_worker import FilesystemWorkerError, execute_filesystem
from xaa.runtime.workspace_snapshot import checked_path
from xaa.storage.runs import RunStore
from xaa.storage.task_workspaces import TaskWorkspaces


class IntegrationResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1, max_length=2048)
    expected_current_sha256: str | None = Field(pattern=r"^[a-f0-9]{64}$")
    choice: Literal["current", "proposed"]


class IntegrationFile(BaseModel):
    path: str
    base_sha256: str | None
    proposed_sha256: str | None
    current_sha256: str | None
    current_excerpt: str
    current_excerpt_truncated: bool
    conflict: bool
    diff: str
    diff_truncated: bool


class IntegrationPreview(BaseModel):
    task_id: str
    changes: list[IntegrationFile]


class IntegrationArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    task_id: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=2048)


def review_state(
    store: RunStore, run_id: str, task_id: str
) -> tuple[dict[str, bytes], dict[str, bytes]]:
    workspaces = TaskWorkspaces(store)
    with store.connection() as connection:
        row = connection.execute(
            "SELECT t.status,r.state,r.workspace,r.mode FROM tasks t JOIN runs r ON r.id=t.run_id WHERE t.id=? AND r.id=?",
            (f"{run_id}:{task_id}", run_id),
        ).fetchone()
        if row is None:
            raise KeyError(task_id)
        if row["status"] != "verifying" or row["state"] != "verifying":
            raise ValueError("Integration requires a specialist and parent awaiting review.")
        binding = workspaces.binding(connection, run_id, task_id)
        if binding is None:
            raise ValueError("This task has no private workspace.")
        root = command_workspace(row["workspace"], store.path, row["mode"])
        if str(root) != binding["source_workspace"]:
            raise PermissionError("The original project changed its workspace binding.")
        base, files = workspaces.view(connection, run_id, task_id)
    return {item.path: item.content for item in base.files}, files


async def current_file(
    store: RunStore, run_id: str, owner: str, path: str, deadline: float
) -> dict[str, Any]:
    identity = f"integration-read-{uuid4()}"
    try:
        result = await execute_filesystem(
            store,
            run_id,
            owner,
            identity,
            "read",
            {"path": path},
            idempotency_key=identity,
            internal=True,
            timeout=min(10, max(0, deadline - time.monotonic())),
        )
    except FilesystemWorkerError as exc:
        if exc.code == "FileNotFoundError":
            return {"sha256": None, "text": "", "truncated": False}
        raise
    if result.get("truncated") or not result.get("sha256"):
        raise ValueError("Integration requires a complete file of at most 64 KiB.")
    return result


async def preview(store: RunStore, run_id: str, task_id: str, owner: str) -> IntegrationPreview:
    original, files = review_state(store, run_id, task_id)
    changes = TaskWorkspaces(store).changes(run_id, task_id)
    deadline = time.monotonic() + 90
    rows: list[IntegrationFile] = []
    for change in changes:
        path = change["path"]
        current = await current_file(store, run_id, owner, path, deadline)
        rows.append(
            IntegrationFile(
                **change,
                current_sha256=current["sha256"],
                current_excerpt=current["text"],
                current_excerpt_truncated=False,
                conflict=(
                    current["sha256"] not in (change["base_sha256"], change["proposed_sha256"])
                    or (change["proposed_sha256"] is None and current["sha256"] is None)
                ),
            )
        )
    # The copy is immutable once the task reaches verifying; read all bytes to validate its journal.
    if any(
        path not in files and path not in {item["path"] for item in changes} for path in original
    ):
        raise ValueError("Unexpected specialist file removal.")
    return IntegrationPreview(task_id=task_id, changes=rows)


async def integrate_changes(
    store: RunStore, run_id: str, task_id: str, owner: str, resolutions: list[IntegrationResolution]
) -> list[dict[str, Any]]:
    snapshot = await preview(store, run_id, task_id, owner)
    choices = {item.path: item for item in resolutions}
    if len(choices) != len(resolutions) or set(choices) - {item.path for item in snapshot.changes}:
        raise ValueError("Integration choices must identify unique proposed paths.")
    prefix = "integrate-" + hashlib.sha256(f"{run_id}:{task_id}".encode()).hexdigest()[:16] + "-"
    with store.connection() as connection:
        if connection.execute(
            "SELECT 1 FROM tool_calls WHERE run_id=? AND provider_call_id LIKE ? AND state='intended'",
            (run_id, prefix + "%"),
        ).fetchone():
            raise ValueError(
                "An integration write needs reconciliation before further integration."
            )
    writes: list[dict[str, Any]] = []
    observed: list[dict[str, Any]] = []
    for change in snapshot.changes:
        resolution = choices.get(change.path)
        if resolution and resolution.expected_current_sha256 != change.current_sha256:
            raise ValueError(
                "Project changed after conflict preview. Refresh the preview before choosing."
            )
        if change.conflict and resolution is None:
            raise ValueError(
                "Integration conflict: preview current project files and explicitly choose current or proposed content."
            )
        keep_current = resolution is not None and resolution.choice == "current"
        if keep_current and change.current_sha256 is None:
            raise ValueError("A missing current file cannot be selected as the retained version.")
        if change.proposed_sha256 is None and change.current_sha256 is None:
            # Desired deletion already holds; no filesystem operation is required.
            observed.append(
                {
                    "path": change.path,
                    "current_sha256": None,
                    "proposed_sha256": None,
                    "choice": "proposed",
                }
            )
            continue
        item = {
            "path": change.path,
            "current_sha256": change.current_sha256,
            "proposed_sha256": change.proposed_sha256,
            "choice": "current" if keep_current else "proposed",
        }
        observed.append(item)
        if keep_current or change.current_sha256 == change.proposed_sha256:
            continue
        identity = prefix + uuid4().hex
        writes.append(
            {
                **item,
                "action": "delete" if change.proposed_sha256 is None else "write",
                "call_id": identity,
            }
        )
    with store.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        store.require_lease(connection, run_id, owner)
        review_state(store, run_id, task_id)
        store.record_event(
            connection,
            run_id,
            "task.integration.authorized",
            {
                "task_id": task_id,
                "actor": "local-user",
                "owner_sha256": hashlib.sha256(owner.encode()).hexdigest(),
                "files": observed,
                "writes": writes,
            },
        )
    deadline = time.monotonic() + 90
    for item in writes:
        result = await execute_filesystem(
            store,
            run_id,
            owner,
            item["call_id"],
            "integrate",
            {"task_id": task_id, "path": item["path"]},
            idempotency_key=item["call_id"],
            internal=True,
            timeout=min(10, max(0, deadline - time.monotonic())),
        )
        if item["action"] == "delete" and result.get("deleted") is not True:
            raise ValueError("Integration did not confirm the approved deletion.")
        if item["action"] != "delete" and result.get("sha256") != item["proposed_sha256"]:
            raise ValueError("Integration did not confirm the exact approved file bytes.")
    return observed


def integrate_file(
    store: RunStore, run_id: str, owner: str, call_id: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    parsed = IntegrationArguments.model_validate(arguments)
    path = checked_path(parsed.path).as_posix()
    _, files = review_state(store, run_id, parsed.task_id)
    plans: list[dict[str, Any]] = []
    with store.connection() as connection:
        store.require_lease(connection, run_id, owner)
        for row in connection.execute(
            "SELECT payload FROM run_events WHERE run_id=? AND event_type='task.integration.authorized' ORDER BY sequence",
            (run_id,),
        ):
            event = json.loads(row[0])
            if (
                event.get("task_id") == parsed.task_id
                and event.get("actor") == "local-user"
                and event.get("owner_sha256") == hashlib.sha256(owner.encode()).hexdigest()
            ):
                plans.extend(
                    item
                    for item in event["writes"]
                    if item["call_id"] == call_id and item["path"] == path
                )
        existing = connection.execute(
            "SELECT state FROM tool_calls WHERE run_id=? AND provider_call_id=?", (run_id, call_id)
        ).fetchone()
    if not plans or any(item != plans[0] for item in plans):
        raise PermissionError(
            "Integration lacks an unambiguous saved user decision under this lease."
        )
    if existing is not None:
        raise ValueError(
            "An integration action already exists; inspect/reconcile it instead of replaying it."
        )
    plan = plans[0]
    executor = WorkspaceExecutor(store)
    if plan.get("action") == "delete":
        if plan["proposed_sha256"] is not None or not plan["current_sha256"]:
            raise ValueError("Saved deletion plan has an unexpected content hash.")
        return executor.delete_file(
            run_id,
            owner,
            call_id,
            {"path": path, "expected_sha256": plan["current_sha256"]},
            idempotency_key=call_id,
        )
    data = files[path]
    if hashlib.sha256(data).hexdigest() != plan["proposed_sha256"]:
        raise ValueError("The proposed file changed after review.")
    content = data.decode("utf-8")
    if plan["current_sha256"] is None:
        return executor.create_file(
            run_id, owner, call_id, {"path": path, "content": content}, idempotency_key=call_id
        )
    return executor.write_file(
        run_id,
        owner,
        call_id,
        {"path": path, "content": content, "expected_sha256": plan["current_sha256"]},
        idempotency_key=call_id,
    )
