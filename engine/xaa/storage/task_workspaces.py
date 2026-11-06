"""Durable private specialist working trees: immutable snapshot plus atomic journal overlays."""

import difflib
import hashlib
import json
import os
import sqlite3
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from uuid import uuid4

from xaa.runtime.command_contracts import command_workspace
from xaa.runtime.directory_listing import ListDirectoryArguments
from xaa.runtime.executor import (
    CreateFileArguments,
    DeleteFileArguments,
    ReadFileArguments,
    WriteFileArguments,
)
from xaa.runtime.file_handles import open_for_read, opened_file_path
from xaa.runtime.workspace_snapshot import (
    SnapshotFile,
    WorkspaceSnapshot,
    capture_workspace,
    checked_path,
)
from xaa.storage.commands import command_artifact_root
from xaa.storage.runs import RunStore, utc_now

BranchOperation = Literal[
    "branch_read", "branch_list", "branch_write", "branch_create", "branch_delete"
]


class TaskWorkspaces:
    def __init__(self, store: RunStore):
        self.store = store

    def require_writer(
        self, connection: sqlite3.Connection, run_id: str, owner: str, task_id: str, worker: str
    ) -> Path:
        self.store.require_lease(connection, run_id, owner)
        self.store.require_task_read_scope(connection, run_id, task_id, worker)
        row = connection.execute(
            "SELECT r.workspace,r.mode,t.permission_scope FROM tasks t JOIN runs r ON r.id=t.run_id WHERE t.id=? AND r.id=?",
            (f"{run_id}:{task_id}", run_id),
        ).fetchone()
        if row is None or "workspace.write" not in json.loads(row["permission_scope"]):
            raise PermissionError("This specialist has no saved write permission.")
        return command_workspace(row["workspace"], self.store.path, row["mode"])

    def binding(
        self, connection: sqlite3.Connection, run_id: str, task_id: str
    ) -> dict[str, Any] | None:
        rows = connection.execute(
            "SELECT payload FROM run_events WHERE run_id=? AND event_type='task.workspace.created' AND json_extract(payload,'$.task_id')=?",
            (run_id, task_id),
        ).fetchall()
        if len(rows) > 1:
            raise PermissionError("Ambiguous specialist workspace binding.")
        return json.loads(rows[0][0]) if rows else None

    def initialize(self, run_id: str, owner: str, task_id: str, worker: str) -> dict[str, Any]:
        with self.store.connection() as connection:
            root = self.require_writer(connection, run_id, owner, task_id, worker)
            binding = self.binding(connection, run_id, task_id)
            if binding:
                return {
                    "snapshot_sha256": binding["snapshot_sha256"],
                    "effect": "private_copy_only",
                }
        snapshot = capture_workspace(root)
        if sum(len(item.content) for item in snapshot.files) > 4 * 1024 * 1024:
            raise ValueError(
                "Isolated specialist workspaces currently support at most 4 MiB of project files."
            )
        raw = snapshot.to_bytes()
        folder = command_artifact_root(self.store, create=True)
        name = f"{uuid4()}.task-snapshot.json"
        with (folder / name).open("xb") as destination:
            destination.write(raw)
            destination.flush()
            os.fsync(destination.fileno())
        artifact = self.store.register_artifact(
            run_id, owner, name, provenance=f"task.workspace.base:{task_id}"
        )
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if self.require_writer(connection, run_id, owner, task_id, worker) != root:
                raise PermissionError("Workspace changed during capture.")
            if self.binding(connection, run_id, task_id):
                raise PermissionError("Specialist workspace was already captured.")
            total = connection.execute(
                "SELECT COALESCE(SUM(size_bytes),0) FROM artifacts WHERE run_id=? AND provenance LIKE 'task.workspace.base:%'",
                (run_id,),
            ).fetchone()[0]
            if total > 32 * 1024 * 1024:
                raise ValueError("Shared specialist snapshot storage exceeded 32 MiB.")
            connection.execute(
                "UPDATE artifacts SET task_id=? WHERE id=?", (f"{run_id}:{task_id}", artifact["id"])
            )
            self.store.record_event(
                connection,
                run_id,
                "task.workspace.created",
                {
                    "task_id": task_id,
                    "artifact_id": artifact["id"],
                    "snapshot_sha256": snapshot.sha256,
                    "source_workspace": str(root),
                    "format": 1,
                },
            )
        return {"snapshot_sha256": snapshot.sha256, "effect": "private_copy_only"}

    def view(
        self, connection: sqlite3.Connection, run_id: str, task_id: str
    ) -> tuple[WorkspaceSnapshot, dict[str, bytes]]:
        binding = self.binding(connection, run_id, task_id)
        if binding is None:
            raise PermissionError("Specialist workspace has not been captured.")
        artifact = connection.execute(
            "SELECT * FROM artifacts WHERE id=? AND run_id=? AND task_id=?",
            (binding["artifact_id"], run_id, f"{run_id}:{task_id}"),
        ).fetchone()
        if artifact is None or Path(artifact["storage_key"]).name != artifact["storage_key"]:
            raise PermissionError("Specialist snapshot artifact is unavailable.")
        folder = command_artifact_root(self.store)
        with open_for_read(folder / artifact["storage_key"]) as source:
            if not opened_file_path(source).is_relative_to(folder):
                raise PermissionError("Snapshot artifact escaped private storage.")
            raw = source.read(8 * 1024 * 1024 + 1)
        if (
            len(raw) != artifact["size_bytes"]
            or hashlib.sha256(raw).hexdigest() != artifact["sha256"]
        ):
            raise ValueError("Specialist snapshot bytes changed.")
        base = WorkspaceSnapshot.from_bytes(raw)
        if base.sha256 != binding["snapshot_sha256"]:
            raise ValueError("Specialist snapshot identity changed.")
        files = {item.path: item.content for item in base.files}
        rows = connection.execute(
            "SELECT t.tool_name,t.arguments,t.result FROM tool_calls t JOIN run_events e ON e.run_id=t.run_id AND e.event_type='tool.completed' AND json_extract(e.payload,'$.call_id')=t.provider_call_id WHERE t.run_id=? AND t.task_id=? AND t.state='completed' AND t.tool_name IN ('workspace.write_file','workspace.create_file','workspace.delete_file') ORDER BY e.sequence",
            (run_id, f"{run_id}:{task_id}"),
        ).fetchall()
        for row in rows:
            args = json.loads(row["arguments"])
            result = json.loads(row["result"])
            path = checked_path(args["path"]).as_posix()
            if row["tool_name"] == "workspace.delete_file":
                previous = files.get(path)
                expected = args.get("expected_sha256")
                if (
                    previous is None
                    or not isinstance(expected, str)
                    or hashlib.sha256(previous).hexdigest() != expected
                    or result.get("previous_sha256") != expected
                    or result.get("effect") != "private_copy_only"
                    or result.get("deleted") is not True
                ):
                    raise ValueError("Specialist deletion journal is inconsistent.")
                del files[path]
                continue
            data = args["content"].encode("utf-8")
            if (
                hashlib.sha256(data).hexdigest() != result["sha256"]
                or result.get("effect") != "private_copy_only"
                or result.get("bytes_written") != len(data)
            ):
                raise ValueError("Specialist edit journal is inconsistent.")
            if row["tool_name"] == "workspace.write_file":
                previous = files.get(path)
                if (
                    previous is None
                    or hashlib.sha256(previous).hexdigest() != args.get("expected_sha256")
                    or result.get("created") is not False
                ):
                    raise ValueError("Specialist write journal transition is inconsistent.")
            elif path in files or result.get("created") is not True:
                raise ValueError("Specialist creation journal transition is inconsistent.")
            files[path] = data
        return base, files

    def operate(
        self,
        run_id: str,
        owner: str,
        task_id: str,
        worker: str,
        call_id: str,
        operation: BranchOperation,
        arguments: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        parsed = (
            ReadFileArguments
            if operation == "branch_read"
            else ListDirectoryArguments
            if operation == "branch_list"
            else WriteFileArguments
            if operation == "branch_write"
            else CreateFileArguments
            if operation == "branch_create"
            else DeleteFileArguments
        ).model_validate(arguments)
        path = "." if parsed.path == "." else checked_path(parsed.path).as_posix()
        tool = {
            "branch_read": "workspace.read_file",
            "branch_list": "workspace.list_directory",
            "branch_write": "workspace.write_file",
            "branch_create": "workspace.create_file",
            "branch_delete": "workspace.delete_file",
        }[operation]
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.require_writer(connection, run_id, owner, task_id, worker)
            base, files = self.view(connection, run_id, task_id)
            if connection.execute(
                "SELECT 1 FROM tool_calls WHERE run_id=? AND (provider_call_id=? OR idempotency_key=?)",
                (run_id, call_id, idempotency_key),
            ).fetchone():
                raise ValueError("Specialist call already exists; never replay it.")
            if operation == "branch_read":
                if path not in files:
                    raise FileNotFoundError(path)
                data = files[path]
                result = {
                    "path": path,
                    "text": data[:65536].decode("utf-8", errors="replace"),
                    "truncated": len(data) > 65536,
                    "sha256": hashlib.sha256(data).hexdigest() if len(data) <= 65536 else None,
                    "trust": "untrusted_workspace_content",
                }
            elif operation == "branch_list":
                if path != "." and path not in base.directories:
                    raise FileNotFoundError(path)
                entries = [
                    {"name": PurePosixPath(name).name, "kind": kind}
                    for names, kind in ((files, "file"), (base.directories, "directory"))
                    for name in names
                    if str(PurePosixPath(name).parent) == path
                ]
                result = {
                    "path": path,
                    "entries": sorted(entries, key=lambda item: item["name"]),
                    "truncated": False,
                    "trust": "untrusted_workspace_content",
                }
            elif operation == "branch_delete":
                delete = DeleteFileArguments.model_validate(arguments)
                if (
                    path not in files
                    or hashlib.sha256(files[path]).hexdigest() != delete.expected_sha256
                ):
                    raise ValueError("Specialist file changed; read it before deleting.")
                if len(files[path]) > 65536:
                    raise ValueError("Specialist deletion requires a file of at most 64 KiB.")
                del files[path]
                original = {item.path: item.content for item in base.files}
                dirty_paths = {
                    name
                    for name in original.keys() | files.keys()
                    if original.get(name) != files.get(name)
                }
                dirty_size = sum(
                    len(files[name]) if name in files else len(original[name])
                    for name in dirty_paths
                )
                if len(dirty_paths) > 16 or dirty_size > 1024 * 1024:
                    raise ValueError("Specialist changes exceed 16 files or 1 MiB.")
                result = {
                    "path": path,
                    "deleted": True,
                    "previous_sha256": delete.expected_sha256,
                    "effect": "private_copy_only",
                }
            else:
                write = (
                    WriteFileArguments.model_validate(arguments)
                    if operation == "branch_write"
                    else CreateFileArguments.model_validate(arguments)
                )
                data = write.content.encode("utf-8")
                if len(data) > 65536:
                    raise ValueError("Specialist edits exceed the 64 KiB file limit.")
                if operation == "branch_create" and path in files:
                    raise ValueError("Specialist creation cannot replace an existing file.")
                if operation == "branch_write" and (
                    path not in files
                    or hashlib.sha256(files[path]).hexdigest()
                    != WriteFileArguments.model_validate(arguments).expected_sha256
                ):
                    raise ValueError("Specialist file changed; read it before editing.")
                if operation == "branch_write" and len(files[path]) > 65536:
                    raise ValueError("Specialist edits require an existing file of at most 64 KiB.")
                files[path] = data
                WorkspaceSnapshot(
                    tuple(SnapshotFile(name, content) for name, content in files.items()),
                    base.directories,
                    base.excluded,
                )
                original = {item.path: item.content for item in base.files}
                dirty_paths = {
                    name
                    for name in original.keys() | files.keys()
                    if original.get(name) != files.get(name)
                }
                dirty_size = sum(
                    len(files[name]) if name in files else len(original[name])
                    for name in dirty_paths
                )
                if len(dirty_paths) > 16 or dirty_size > 1024 * 1024:
                    raise ValueError("Specialist changes exceed 16 files or 1 MiB.")
                result = {
                    "path": path,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "bytes_written": len(data),
                    "effect": "private_copy_only",
                    "created": operation == "branch_create",
                }
            now = utc_now()
            connection.execute(
                "INSERT INTO tool_calls (id,task_id,run_id,tool_name,tool_version,arguments,idempotency_key,state,result,error,created_at,updated_at,provider_call_id) VALUES (?,?,?,?, '1',?,?,'intended',NULL,NULL,?,?,?)",
                (
                    str(uuid4()),
                    f"{run_id}:{task_id}",
                    run_id,
                    tool,
                    json.dumps(arguments),
                    idempotency_key,
                    now,
                    now,
                    call_id,
                ),
            )
            self.store.record_event(
                connection,
                run_id,
                "tool.intended",
                {"call_id": call_id, "tool": tool, "internal": True, "task_id": task_id},
            )
            connection.execute(
                "UPDATE tool_calls SET state='completed',result=? WHERE run_id=? AND provider_call_id=?",
                (json.dumps(result), run_id, call_id),
            )
            self.store.record_event(
                connection,
                run_id,
                "tool.completed",
                {"call_id": call_id, "internal": True, "task_id": task_id},
            )
            return result

    def changes(self, run_id: str, task_id: str) -> list[dict[str, Any]]:
        with self.store.connection() as connection:
            if self.binding(connection, run_id, task_id) is None:
                return []
            base, files = self.view(connection, run_id, task_id)
        original = {item.path: item.content for item in base.files}
        changes: list[dict[str, Any]] = []
        for name in sorted(set(original) | set(files)):
            before = original.get(name)
            data = files.get(name)
            if before == data:
                continue
            diff = "".join(
                line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                for line in difflib.unified_diff(
                    (before or b"").decode("utf-8", errors="replace").splitlines(keepends=True),
                    data.decode("utf-8", errors="replace").splitlines(keepends=True)
                    if data is not None
                    else [],
                    fromfile="a/" + name,
                    tofile="b/" + name,
                )
            )
            changes.append(
                {
                    "path": name,
                    "base_sha256": hashlib.sha256(before).hexdigest()
                    if before is not None
                    else None,
                    "proposed_sha256": hashlib.sha256(data).hexdigest()
                    if data is not None
                    else None,
                    "diff": diff,
                    "diff_truncated": False,
                }
            )
        return changes
