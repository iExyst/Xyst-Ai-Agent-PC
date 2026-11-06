"""Permission-checked bounded workspace operations through the durable journal."""

import errno
import hashlib
import os
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from xaa.runtime.create_handles import creation_parts, open_new_file
from xaa.runtime.file_handles import (
    delete_opened_file_if_hash,
    open_existing_for_edit,
    open_for_read,
    opened_file_path,
)
from xaa.runtime.unified_patch import apply_unified_patch
from xaa.storage.runs import RunStore


class ReadFileArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1, max_length=2048)


class WriteFileArguments(ReadFileArguments):
    content: str = Field(max_length=65536)
    expected_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class CreateFileArguments(ReadFileArguments):
    content: str = Field(max_length=65536)


class DeleteFileArguments(ReadFileArguments):
    expected_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReplaceTextArguments(ReadFileArguments):
    old_text: str = Field(min_length=1, max_length=65536)
    new_text: str = Field(max_length=65536)
    expected_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ApplyPatchArguments(ReadFileArguments):
    patch: str = Field(min_length=1, max_length=65536)
    expected_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


PATCH_METADATA = {
    "name": "workspace.apply_patch",
    "version": "1",
    "permissions": ["workspace.read", "workspace.write"],
    "behavior": "write existing file after complete patch validation",
    "input_schema": ApplyPatchArguments.model_json_schema(),
    "output_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "sha256": {"type": "string"},
            "bytes_written": {"type": "integer"},
        },
        "required": ["path", "sha256", "bytes_written"],
    },
    "timeout": "bounded synchronous filesystem operation; worker timeout isolation pending",
    "cancellation": "lease checked before write; no interruption inside compare/write/fsync",
    "retry": "no automatic retries; reconcile normalized write intent first",
    "limits": {"input_bytes": 65536, "output_bytes": 65536, "hunks": 128},
    "audit": ["run_id", "call_id", "arguments", "tool.executing", "sha256", "bytes_written"],
}

CREATE_METADATA = {
    **PATCH_METADATA,
    "name": "workspace.create_file",
    "permissions": ["workspace.write"],
    "behavior": "exclusive creation in existing verified Windows directories",
    "input_schema": CreateFileArguments.model_json_schema(),
    "output_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "sha256": {"type": "string"},
            "bytes_written": {"type": "integer"},
            "created": {"const": True},
        },
        "required": ["path", "sha256", "bytes_written", "created"],
    },
    "cancellation": "lease checked before creation; failures after exclusive create remain uncertain",
    "retry": "none; confirm exact destination bytes without rewriting on recovery",
    "limits": {"input_bytes": 65536, "output_bytes": 65536, "path_components": 32},
}


class ReconciliationRequired(RuntimeError):
    """An existing action must be inspected before attempting execution again."""


class ToolPreconditionError(ValueError):
    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__(result["error"]["message"])
        self.result = result


class WorkspaceExecutor:
    """Derive workspace and permissions from the saved run, never from model arguments."""

    def __init__(self, store: RunStore) -> None:
        self.store = store

    def read_file(
        self,
        run_id: str,
        token: str,
        call_id: str,
        arguments: dict[str, Any],
        *,
        idempotency_key: str,
        internal: bool = False,
    ) -> dict[str, Any]:
        parsed = ReadFileArguments.model_validate(arguments)
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run["mode"] not in {"observe", "workspace", "authorized_automation"}:
            raise PermissionError("Run has no workspace read permission.")
        if not run["workspace"]:
            raise PermissionError("Select a workspace before reading files.")
        root = Path(run["workspace"]).resolve(strict=True)
        requested = Path(parsed.path)
        if requested.is_absolute():
            raise PermissionError("Use a path relative to the selected workspace.")
        target = (root / requested).resolve(strict=True)
        if not target.is_relative_to(root) or not target.is_file():
            raise PermissionError("Requested file is outside the selected workspace.")
        fresh = self.store.record_tool_intent(
            run_id,
            token,
            call_id=call_id,
            tool="workspace.read_file",
            version="1",
            arguments=parsed.model_dump(),
            idempotency_key=idempotency_key,
            internal=internal,
        )
        if not fresh:
            raise ReconciliationRequired(
                "This tool action already exists; inspect its saved result or unresolved intent."
            )
        # Retry only transient read I/O. Every attempt reopens and verifies the handle.
        for attempt in range(3):
            with self.store.connection() as connection:
                self.store.require_lease(connection, run_id, token)
            try:
                with open_for_read(target) as source:
                    if not opened_file_path(source).is_relative_to(root):
                        raise PermissionError(
                            "Opened file destination escaped the selected workspace."
                        )
                    content = source.read(65_537)
                break
            except OSError as exc:
                transient = getattr(exc, "winerror", None) in {32, 33, 121, 1237} or exc.errno in {
                    errno.EAGAIN,
                    errno.EINTR,
                    errno.ETIMEDOUT,
                    errno.ESTALE,
                }
                if not transient or attempt == 2:
                    raise
                self.store.append_worker_event(
                    run_id,
                    token,
                    "tool.read_retry",
                    {
                        "call_id": call_id,
                        "attempt": attempt + 1,
                        "delay_ms": 50 * 2**attempt,
                    },
                )
                time.sleep(0.05 * 2**attempt)
        else:
            raise RuntimeError("Read retry loop exhausted without a result.")
        result: dict[str, Any] = {
            "path": parsed.path,
            "text": content[:65_536].decode("utf-8", errors="replace"),
            "truncated": len(content) > 65_536,
            "trust": "untrusted_workspace_content",
            "sha256": hashlib.sha256(content).hexdigest() if len(content) <= 65536 else None,
        }
        self.store.record_tool_result(run_id, token, call_id, result, internal=internal)
        return result

    def reconcile_writes(self, run_id: str, token: str) -> dict[str, str]:
        """Confirm exact applied effects without re-executing an uncertain write."""
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run["mode"] != "workspace" or not run["workspace"]:
            return {}
        root = Path(run["workspace"]).resolve(strict=True)
        outcomes: dict[str, str] = {}
        for call in self.store.unresolved_tool_calls(run_id):
            if call["tool_name"] == "workspace.delete_file" and call["tool_version"] == "1":
                deleted = DeleteFileArguments.model_validate(call["arguments"])
                target = root / deleted.path
                try:
                    resolved_target = target.resolve(strict=True)
                except FileNotFoundError:
                    self.store.record_tool_result(run_id, token, call["id"], {"path": deleted.path, "deleted": True, "previous_sha256": deleted.expected_sha256, "reconciled": True})
                    outcomes[call["id"]] = "applied"
                    continue
                if not resolved_target.is_relative_to(root) or not resolved_target.is_file():
                    outcomes[call["id"]] = "conflicting"
                    continue
                with open_for_read(resolved_target) as source:
                    if not opened_file_path(source).is_relative_to(root):
                        raise PermissionError("Opened deletion reconciliation target escaped workspace.")
                    content = source.read(65537)
                digest = hashlib.sha256(content).hexdigest()
                outcomes[call["id"]] = "unchanged" if digest == deleted.expected_sha256 and len(content) <= 65536 else "conflicting"
                continue
            if call["tool_name"] == "workspace.create_file" and call["tool_version"] == "1":
                created = CreateFileArguments.model_validate(call["arguments"])
                parts = creation_parts(created.path)
                try:
                    target = root.joinpath(*parts).resolve(strict=True)
                except FileNotFoundError:
                    outcomes[call["id"]] = "missing"
                    continue
                if not target.is_relative_to(root):
                    raise PermissionError("Cannot reconcile creation outside workspace.")
                with open_existing_for_edit(target, database=self.store.path) as source:
                    if not opened_file_path(source).is_relative_to(root):
                        raise PermissionError("Opened creation target escaped workspace.")
                    current = source.read(65537)
                desired = created.content.encode("utf-8")
                if current == desired and len(current) <= 65536:
                    self.store.record_tool_result(
                        run_id,
                        token,
                        call["id"],
                        {
                            "path": created.path,
                            "sha256": hashlib.sha256(desired).hexdigest(),
                            "bytes_written": len(desired),
                            "created": True,
                            "reconciled": True,
                        },
                    )
                    outcomes[call["id"]] = "applied"
                else:
                    outcomes[call["id"]] = "conflicting"
                continue
            if call["tool_name"] != "workspace.write_file" or call["tool_version"] != "1":
                continue
            parsed = WriteFileArguments.model_validate(call["arguments"])
            requested = Path(parsed.path)
            if requested.is_absolute():
                raise PermissionError("Cannot reconcile an absolute edit target.")
            target = (root / requested).resolve(strict=True)
            if not target.is_relative_to(root):
                raise PermissionError("Cannot reconcile an edit outside the workspace.")
            with open_existing_for_edit(target, database=self.store.path) as source:
                if not opened_file_path(source).is_relative_to(root):
                    raise PermissionError("Opened reconciliation target escaped the workspace.")
                current = source.read(65537)
            desired = parsed.content.encode("utf-8")
            desired_hash = hashlib.sha256(desired).hexdigest()
            current_hash = hashlib.sha256(current).hexdigest()
            if len(current) <= 65536 and current_hash == desired_hash:
                self.store.record_tool_result(
                    run_id,
                    token,
                    call["id"],
                    {
                        "path": parsed.path,
                        "sha256": desired_hash,
                        "bytes_written": len(desired),
                        "reconciled": True,
                    },
                )
                outcomes[call["id"]] = "applied"
            else:
                outcomes[call["id"]] = (
                    "unchanged" if current_hash == parsed.expected_sha256 else "conflicting"
                )
        return outcomes

    def delete_file(
        self,
        run_id: str,
        token: str,
        call_id: str,
        arguments: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        parsed = DeleteFileArguments.model_validate(arguments)
        run = self.store.get_run(run_id)
        if run is None or run["mode"] != "workspace" or not run["workspace"]:
            raise PermissionError("Reviewed deletion requires a selected workspace-mode run.")
        root = Path(run["workspace"]).resolve(strict=True)
        requested = Path(parsed.path)
        if requested.is_absolute():
            raise PermissionError("Use a workspace-relative path.")
        target = root / requested
        try:
            resolved_target = target.resolve(strict=True)
        except FileNotFoundError:
            raise PermissionError("Reviewed deletion target is already missing; refresh the preview.")
        if not resolved_target.is_relative_to(root) or not resolved_target.is_file():
            raise PermissionError("Deletion target is outside the selected workspace.")
        if not self.store.record_tool_intent(
            run_id,
            token,
            call_id=call_id,
            tool="workspace.delete_file",
            version="1",
            arguments=parsed.model_dump(),
            idempotency_key=idempotency_key,
            internal=True,
        ):
            raise ReconciliationRequired("Existing deletion must be reconciled, never replayed.")
        execution_started = False

        def mark_execution() -> None:
            nonlocal execution_started
            self.store.append_worker_event(
                run_id, token, "tool.executing", {"call_id": call_id, "internal": True}
            )
            execution_started = True

        try:
            count = delete_opened_file_if_hash(
                resolved_target,
                root,
                parsed.expected_sha256,
                database=self.store.path,
                before_delete=mark_execution,
            )
        except (OSError, PermissionError, ValueError) as exc:
            if execution_started:
                raise
            raise ToolPreconditionError(self.store.record_tool_failure(run_id, token, call_id, code="delete_precondition", message="Reviewed deletion failed before the approved file was removed.")) from exc
        result = {
            "path": parsed.path,
            "deleted": True,
            "previous_sha256": parsed.expected_sha256,
            "bytes_deleted": count,
        }
        self.store.record_tool_result(run_id, token, call_id, result, internal=True)
        return result

    def create_file(
        self,
        run_id: str,
        token: str,
        call_id: str,
        arguments: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        parsed = CreateFileArguments.model_validate(arguments)
        encoded = parsed.content.encode("utf-8")
        if len(encoded) > 65536:
            raise ValueError("File creation exceeds the 64 KiB byte limit.")
        parts = creation_parts(parsed.path)
        run = self.store.get_run(run_id)
        if run is None or run["mode"] != "workspace" or not run["workspace"]:
            raise PermissionError("File creation requires a selected workspace-mode run.")
        root = Path(run["workspace"]).resolve(strict=True)
        if not self.store.record_tool_intent(
            run_id,
            token,
            call_id=call_id,
            tool="workspace.create_file",
            version="1",
            arguments=parsed.model_dump(),
            idempotency_key=idempotency_key,
        ):
            raise ReconciliationRequired(
                "Existing creation must be reconciled before repeating it."
            )
        with ExitStack() as stack:
            try:
                destination = stack.enter_context(
                    open_new_file(root, parts, database=self.store.path)
                )
            except (OSError, PermissionError) as exc:
                raise ToolPreconditionError(
                    self.store.record_tool_failure(
                        run_id,
                        token,
                        call_id,
                        code="creation_precondition",
                        message="Creation requires existing plain directories and an absent destination; no file was created.",
                    )
                ) from exc
            # Creation itself is already a side effect. Any failure from here stays uncertain.
            self.store.append_worker_event(run_id, token, "tool.executing", {"call_id": call_id})
            if not opened_file_path(destination).is_relative_to(root):
                raise PermissionError("Opened new file escaped workspace; reconciliation required.")
            destination.write(encoded)
            destination.flush()
            os.fsync(destination.fileno())
        result = {
            "path": parsed.path,
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "bytes_written": len(encoded),
            "created": True,
        }
        self.store.record_tool_result(run_id, token, call_id, result)
        return result

    def replace_text(
        self,
        run_id: str,
        token: str,
        call_id: str,
        arguments: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        parsed = ReplaceTextArguments.model_validate(arguments)
        run = self.store.get_run(run_id)
        if run is None or run["mode"] != "workspace":
            raise PermissionError("Text replacement requires workspace mode.")

        def reject(code: str, message: str) -> ToolPreconditionError:
            if not self.store.record_tool_intent(
                run_id,
                token,
                call_id=call_id,
                tool="workspace.replace_text",
                version="1",
                arguments=parsed.model_dump(),
                idempotency_key=idempotency_key,
            ):
                raise ReconciliationRequired(
                    "Existing replacement must be inspected before retrying."
                )
            return ToolPreconditionError(
                self.store.record_tool_failure(run_id, token, call_id, code=code, message=message)
            )

        source = self.read_file(
            run_id,
            token,
            call_id + ":source",
            {"path": parsed.path},
            idempotency_key=idempotency_key + ":source",
            internal=True,
        )
        if source["truncated"] or source["sha256"] != parsed.expected_sha256:
            raise reject(
                "file_changed", "File changed or exceeds replacement limit; read it again."
            )
        if hashlib.sha256(source["text"].encode("utf-8")).hexdigest() != source["sha256"]:
            raise reject("invalid_encoding", "Text replacement requires valid UTF-8 contents.")
        if source["text"].count(parsed.old_text) != 1:
            raise reject("match_count", "Replacement requires exactly one occurrence of old_text.")
        content = source["text"].replace(parsed.old_text, parsed.new_text, 1)
        if len(content.encode("utf-8")) > 65536:
            raise reject("output_too_large", "Replacement exceeds the 64 KiB byte limit.")
        return self.write_file(
            run_id,
            token,
            call_id,
            {"path": parsed.path, "content": content, "expected_sha256": parsed.expected_sha256},
            idempotency_key=idempotency_key,
        )

    def apply_patch(
        self,
        run_id: str,
        token: str,
        call_id: str,
        arguments: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        parsed = ApplyPatchArguments.model_validate(arguments)
        run = self.store.get_run(run_id)
        if run is None or run["mode"] != "workspace":
            raise PermissionError("Patch application requires workspace mode.")
        source = self.read_file(
            run_id,
            token,
            call_id + ":source",
            {"path": parsed.path},
            idempotency_key=idempotency_key + ":source",
            internal=True,
        )
        try:
            if source["truncated"] or source["sha256"] != parsed.expected_sha256:
                raise ValueError("File changed or exceeds patch limit; read it again.")
            if hashlib.sha256(source["text"].encode("utf-8")).hexdigest() != source["sha256"]:
                raise ValueError("Patch application requires valid UTF-8 contents.")
            content = apply_unified_patch(source["text"], parsed.patch, parsed.path)
        except ValueError as exc:
            if not self.store.record_tool_intent(
                run_id,
                token,
                call_id=call_id,
                tool="workspace.apply_patch",
                version="1",
                arguments=parsed.model_dump(),
                idempotency_key=idempotency_key,
            ):
                raise ReconciliationRequired(
                    "Existing patch must be inspected before retrying."
                ) from exc
            raise ToolPreconditionError(
                self.store.record_tool_failure(
                    run_id,
                    token,
                    call_id,
                    code="patch_rejected",
                    message=str(exc),
                )
            ) from exc
        # Normalize validated content to the recoverable compare/write operation.
        return self.write_file(
            run_id,
            token,
            call_id,
            {"path": parsed.path, "content": content, "expected_sha256": parsed.expected_sha256},
            idempotency_key=idempotency_key,
        )

    def write_file(
        self,
        run_id: str,
        token: str,
        call_id: str,
        arguments: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        parsed = WriteFileArguments.model_validate(arguments)
        encoded = parsed.content.encode("utf-8")
        if len(encoded) > 65536:
            raise ValueError("File edit exceeds the 64 KiB byte limit.")
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run["mode"] != "workspace" or not run["workspace"]:
            raise PermissionError("File edits require an explicitly selected workspace-mode run.")
        root = Path(run["workspace"]).resolve(strict=True)
        requested = Path(parsed.path)
        if requested.is_absolute():
            raise PermissionError("Use a workspace-relative path.")
        target = (root / requested).resolve(strict=True)
        if not target.is_relative_to(root) or not target.is_file():
            raise PermissionError("File edit target is outside the selected workspace.")
        if not self.store.record_tool_intent(
            run_id,
            token,
            call_id=call_id,
            tool="workspace.write_file",
            version="1",
            arguments=parsed.model_dump(),
            idempotency_key=idempotency_key,
        ):
            raise ReconciliationRequired(
                "Existing file-edit intent must be reconciled before repeating it."
            )

        def reject(code: str, message: str) -> ToolPreconditionError:
            return ToolPreconditionError(
                self.store.record_tool_failure(run_id, token, call_id, code=code, message=message)
            )

        with ExitStack() as stack:
            try:
                destination = stack.enter_context(
                    open_existing_for_edit(target, database=self.store.path)
                )
            except OSError as exc:
                raise reject(
                    "open_failed", "The file could not be opened for editing; no write started."
                ) from exc
            try:
                contained = opened_file_path(destination).is_relative_to(root)
                previous = destination.read(65537) if contained else b""
            except OSError as exc:
                raise reject(
                    "preflight_read_failed", "File preflight failed; no write started."
                ) from exc
            if not contained:
                raise reject(
                    "scope_changed",
                    "Opened edit destination escaped the workspace; no write started.",
                )
            if (
                len(previous) > 65536
                or hashlib.sha256(previous).hexdigest() != parsed.expected_sha256
            ):
                raise reject(
                    "file_changed",
                    "File changed or exceeds the edit limit; read it again before editing.",
                )
            self.store.append_worker_event(run_id, token, "tool.executing", {"call_id": call_id})
            destination.seek(0)
            destination.write(encoded)
            destination.truncate()
            destination.flush()
            os.fsync(destination.fileno())
        result = {
            "path": parsed.path,
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "bytes_written": len(encoded),
        }
        self.store.record_tool_result(run_id, token, call_id, result)
        return result
