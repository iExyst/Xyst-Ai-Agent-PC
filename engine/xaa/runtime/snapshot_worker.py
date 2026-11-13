"""Owned snapshot capture: project paths come only from lease-bound saved intents."""

import base64
import hashlib
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from xaa.runtime.owned_transport import request_process
from xaa.runtime.workspace_snapshot import (
    MAX_FILES,
    MAX_TOTAL_BYTES,
    MAX_WIRE_BYTES,
    SnapshotFile,
    WorkspaceSnapshot,
    capture_workspace,
    checked_path,
)
from xaa.storage.commands import CommandJournal
from xaa.storage.runs import RunStore, TaskReadScope


def selected_skill_script_resources(
    trial: dict[str, Any] | None,
) -> tuple[str, int, list[tuple[str, bytes]]] | None:
    """Return selected, integrity-checked Python/shell resources in their immutable version."""
    if trial is None:
        return None
    from xaa.storage.skills import snapshot_digest

    saved_digest = trial.get("snapshot_sha256")
    if not isinstance(saved_digest, str) or snapshot_digest(trial) != saved_digest:
        raise ValueError("Selected procedure snapshot failed its integrity check.")
    skill_id = trial.get("skill_id")
    version = trial.get("version")
    assets = trial.get("assets", [])
    if not isinstance(skill_id, str) or type(version) is not int or not isinstance(assets, list):
        raise TypeError("Selected procedure identity or resources are invalid.")

    scripts: list[tuple[str, bytes]] = []
    for asset in cast(list[object], assets):
        if not isinstance(asset, dict):
            raise TypeError("Selected procedure resource is invalid.")
        raw_fields = cast(dict[object, object], asset)
        if any(not isinstance(key, str) for key in raw_fields):
            raise TypeError("Selected procedure resource is invalid.")
        fields = cast(dict[str, object], raw_fields)
        path = fields.get("path")
        media_type = fields.get("media_type")
        encoded = fields.get("content_base64")
        digest = fields.get("sha256")
        if not isinstance(path, str) or not isinstance(media_type, str):
            raise TypeError("Selected procedure resource metadata is invalid.")
        checked_path(path)
        suffix = PurePosixPath(path).suffix.casefold()
        if suffix not in {".py", ".sh"}:
            continue
        if not media_type.startswith("text/") or not isinstance(encoded, str):
            raise ValueError("Selected script must be a UTF-8 text resource.")
        raw = base64.b64decode(encoded, validate=True)
        if (
            len(raw) > 64 * 1024
            or not isinstance(digest, str)
            or hashlib.sha256(raw).hexdigest() != digest
        ):
            raise ValueError("Selected script resource failed its integrity check.")
        raw.decode("utf-8-sig")
        scripts.append((path, raw))
    return skill_id, version, scripts


def selected_skill_script_paths(trial: dict[str, Any] | None) -> list[str]:
    """Return exact sandbox paths for a selected procedure's verified executable resources."""
    selected = selected_skill_script_resources(trial)
    if selected is None:
        return []
    skill_id, version, scripts = selected
    return [f"__xaa_selected_skill__/{skill_id}/v{version}/{path}" for path, _ in scripts]


def add_selected_skill_scripts(
    snapshot: WorkspaceSnapshot, trial: dict[str, Any] | None
) -> WorkspaceSnapshot:
    """Overlay integrity-checked selected Python/shell resources into the private snapshot."""
    selected = selected_skill_script_resources(trial)
    if selected is None:
        return snapshot
    skill_id, version, scripts = selected
    if not scripts:
        return snapshot

    staged: list[SnapshotFile] = []
    staged_dirs: set[str] = set()
    for path, content in scripts:
        relative = f"__xaa_selected_skill__/{skill_id}/v{version}/{path}"
        checked = checked_path(relative)
        for parent in checked.parents:
            if str(parent) != ".":
                staged_dirs.add(str(parent))
        staged.append(SnapshotFile(relative, content))
    if len(snapshot.files) + len(staged) > MAX_FILES:
        raise ValueError("Selected scripts exceed the command snapshot file limit.")
    if sum(len(item.content) for item in (*snapshot.files, *staged)) > MAX_TOTAL_BYTES:
        raise ValueError("Selected scripts exceed the command snapshot byte limit.")
    existing = {
        name.casefold()
        for name in (
            *snapshot.directories,
            *(item.path for item in snapshot.files),
            *snapshot.excluded,
        )
    }
    if any(name.casefold() in existing for name in (*staged_dirs, *(item.path for item in staged))):
        raise ValueError("Project snapshot conflicts with the selected script staging path.")
    return WorkspaceSnapshot(
        (*snapshot.files, *staged),
        (*snapshot.directories, *sorted(staged_dirs)),
        snapshot.excluded,
    )


class SnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    database: str = Field(min_length=1, max_length=32768)
    run_id: str = Field(min_length=1, max_length=128)
    owner: str = Field(min_length=1, max_length=256)
    call_id: str = Field(min_length=1, max_length=256)
    task_id: str | None = Field(default=None, min_length=1, max_length=128)
    task_token: str | None = Field(default=None, min_length=1, max_length=256)
    purpose: Literal["agent", "project_validation"] = "agent"

    def scope(self) -> TaskReadScope | None:
        if (self.task_id is None) != (self.task_token is None):
            raise PermissionError("Both task scope fields are required.")
        return (
            TaskReadScope(self.run_id, self.owner, self.task_id, self.task_token)
            if self.task_id is not None and self.task_token is not None
            else None
        )


async def capture_command_snapshot(
    store: RunStore,
    run_id: str,
    owner: str,
    call_id: str,
    *,
    deadline: float,
    task_scope: TaskReadScope | None = None,
    purpose: Literal["agent", "project_validation"] = "agent",
) -> WorkspaceSnapshot:
    journal = CommandJournal(store, task_scope=task_scope, purpose=purpose)
    journal.snapshot_intent(run_id, owner, call_id)
    request = SnapshotRequest(
        database=str(store.path.resolve()),
        run_id=run_id,
        owner=owner,
        call_id=call_id,
        task_id=task_scope.task_id if task_scope else None,
        task_token=task_scope.task_token if task_scope else None,
        purpose=purpose,
    )
    command = (
        [sys.executable, "--snapshot-worker"]
        if getattr(sys, "frozen", False)
        else [sys.executable, "-I", "-m", "xaa.runtime.snapshot_worker"]
    )
    raw = await request_process(
        command,
        request.model_dump_json().encode("utf-8"),
        timeout=min(10, deadline - time.monotonic()),
        output_limit=MAX_WIRE_BYTES,
    )
    snapshot = WorkspaceSnapshot.from_bytes(raw)
    if time.monotonic() >= deadline:
        raise TimeoutError("Parent deadline expired before snapshot acceptance.")
    journal.snapshot_intent(run_id, owner, call_id)
    return snapshot


def main() -> None:
    raw = sys.stdin.buffer.read(65537)
    if len(raw) > 65536:
        raise ValueError("Snapshot request exceeded its fixed limit.")
    request = SnapshotRequest.model_validate_json(raw)
    store = RunStore(Path(request.database))
    scope = request.scope()
    journal = CommandJournal(store, task_scope=scope, purpose=request.purpose)
    intent = journal.snapshot_intent(request.run_id, request.owner, request.call_id)
    if scope is None:
        snapshot = capture_workspace(Path(intent.workspace))
    else:
        from xaa.runtime.workspace_snapshot import SnapshotFile
        from xaa.storage.task_workspaces import TaskWorkspaces

        with store.connection() as connection:
            journal.require_task(connection, request.run_id, request.owner, request.call_id)
            base, files = TaskWorkspaces(store).view(connection, request.run_id, scope.task_id)
            snapshot = WorkspaceSnapshot(
                tuple(SnapshotFile(name, data) for name, data in files.items()),
                base.directories,
                base.excluded,
            )
    if request.purpose == "agent":
        snapshot = add_selected_skill_scripts(snapshot, store.procedure_trial(request.run_id))
    journal.snapshot_intent(request.run_id, request.owner, request.call_id)
    sys.stdout.buffer.write(snapshot.to_bytes())
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
