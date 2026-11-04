"""Lease-fenced command permission, admission and immutable execution identities."""

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from xaa.runtime.command_contracts import (
    CommandArguments,
    CommandGrant,
    CommandIntent,
    command_workspace,
)
from xaa.runtime.executor import ReconciliationRequired
from xaa.runtime.file_handles import open_for_read, opened_file_path
from xaa.runtime.workspace_snapshot import WorkspaceSnapshot
from xaa.storage.runs import RunStore, utc_now

TOOL = "workspace.run_command"
ACTIVE_COMMAND_LIMIT = 2


def command_artifact_root(store: RunStore, *, create: bool = False) -> Path:
    parent = store.path.parent.resolve(strict=True)
    if store.path.resolve(strict=True).parent != parent:
        raise PermissionError("Command storage cannot use a redirected database file.")
    directory = parent / "artifacts"
    if create:
        directory.mkdir(exist_ok=True)
    info = directory.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 1024:
        raise PermissionError("Command artifact directories cannot be links or reparse points.")
    actual = directory.resolve(strict=True)
    if not actual.is_relative_to(parent):
        raise PermissionError("Command artifacts escaped application storage.")
    return actual


class CommandCapacityError(RuntimeError):
    """Unresolved commands retain admission until an observed result or cleanup."""


class CommandOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    execution_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    filter_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    termination_reason: Literal[
        "finished",
        "cancelled",
        "timeout",
        "oom-kill",
        "signal",
        "resources",
        "output_limit",
        "manager_deadline",
    ]
    exit_code: int | None
    supervisor_exit_code: int
    succeeded: bool
    output_complete: bool
    cleanup_confirmed: Literal[True]
    workspace_changes_persisted: Literal[False] = False
    stdout_artifact_id: str = Field(min_length=1, max_length=128)
    stderr_artifact_id: str = Field(min_length=1, max_length=128)
    stdout_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    stderr_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    stdout_bytes: int = Field(ge=0, le=1048576)
    stderr_bytes: int = Field(ge=0, le=1048576)
    elapsed_seconds: float = Field(ge=0, allow_inf_nan=False)

    @field_validator("cleanup_confirmed", "workspace_changes_persisted", mode="before")
    @classmethod
    def exact_flags(cls, value: Any) -> bool:
        if type(value) is not bool:
            raise ValueError("Execution flags must be JSON booleans.")
        return bool(value)

    @model_validator(mode="after")
    def truthful_outcome(self) -> "CommandOutcome":
        if self.stdout_bytes + self.stderr_bytes > 1048576:
            raise ValueError("Combined command output exceeds the profile limit.")
        normal = self.termination_reason == "finished"
        if normal != (self.exit_code is not None) or (normal and not self.output_complete):
            raise ValueError(
                "Interrupted or truncated execution cannot claim a normal command exit."
            )
        if self.succeeded != (normal and self.exit_code == 0 and self.output_complete):
            raise ValueError("Command success conflicts with its actual outcome.")
        return self


class CommandCleanupEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    execution_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    distribution: str
    profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    closed: bool
    unit_stopped: bool
    cleanup_confirmed: bool
    manager_alive: bool
    manager_outcome_available: bool

    @model_validator(mode="after")
    def stopped_and_sealed(self) -> "CommandCleanupEvidence":
        if (
            not self.closed
            or not self.unit_stopped
            or not self.cleanup_confirmed
            or self.manager_alive
        ):
            raise ValueError(
                "Recovery requires a sealed identity, stopped unit and finished manager."
            )
        return self


class CommandJournal:
    def __init__(self, store: RunStore) -> None:
        self.store = store

    def stopped_candidates(self) -> list[dict[str, Any]]:
        with self.store.connection() as connection:
            rows = connection.execute(
                "SELECT r.id AS run_id,r.revision,t.provider_call_id AS call_id "
                "FROM runs r JOIN tool_calls t ON t.run_id=r.id "
                "WHERE r.state IN ('paused','blocked','failed','cancelled') AND r.lease_owner IS NULL "
                "AND t.state='intended' AND t.tool_name=? AND t.tool_version='1' "
                "AND NOT EXISTS (SELECT 1 FROM run_events e WHERE e.run_id=r.id "
                "AND e.event_type='execution.cleanup_observed' "
                "AND json_extract(e.payload,'$.call_id')=t.provider_call_id "
                "AND json_extract(e.payload,'$.stopped_revision')=r.revision) "
                "ORDER BY t.updated_at LIMIT ?",
                (TOOL, ACTIVE_COMMAND_LIMIT),
            ).fetchall()
        return [dict(row) for row in rows]

    def stopped_intent(self, run_id: str, revision: int, call_id: str) -> CommandIntent:
        with self.store.connection() as connection:
            run = connection.execute(
                "SELECT state,revision,lease_owner FROM runs WHERE id=?", (run_id,)
            ).fetchone()
            if (
                run is None
                or run["revision"] != revision
                or run["lease_owner"] is not None
                or run["state"] not in {"paused", "blocked", "failed", "cancelled"}
            ):
                raise ReconciliationRequired("Stopped command changed before cleanup discovery.")
            intent = self._intent(connection, run_id, call_id)
            grant, workspace = self._grant(
                connection, run_id, self.store.path, validate_workspace=False
            )
            if (intent.distribution, intent.profile_sha256, intent.workspace) != (
                grant.distribution,
                grant.profile_sha256,
                workspace,
            ):
                raise PermissionError("Stopped command recovery authority changed.")
            return intent

    @staticmethod
    def _grant(
        connection: Any, run_id: str, database: Path, *, validate_workspace: bool = True
    ) -> tuple[CommandGrant, str]:
        run = connection.execute("SELECT workspace,mode FROM runs WHERE id=?", (run_id,)).fetchone()
        rows = connection.execute(
            "SELECT payload FROM run_events WHERE run_id=? AND event_type='execution.granted'",
            (run_id,),
        ).fetchall()
        if run is None or len(rows) != 1:
            raise PermissionError("This run has no explicit command execution grant.")
        selected = json.loads(rows[0][0])
        if selected.get("actor") != "local_user":
            raise PermissionError("Command grants must come from the local user.")
        grant = CommandGrant.model_validate(selected["grant"])
        if not validate_workspace:
            # Closing an already-reserved identity must work after project deletion/rename.
            saved = selected.get("workspace")
            if (
                run["mode"] != "workspace"
                or not isinstance(saved, str)
                or not Path(saved).is_absolute()
            ):
                raise PermissionError("Saved recovery grant is invalid.")
            return grant, saved
        root = command_workspace(run["workspace"], database, run["mode"])
        if str(root) != selected["workspace"]:
            raise PermissionError("Command workspace changed after permission was recorded.")
        return grant, str(root)

    def grant(self, run_id: str, *, owner: str | None = None) -> CommandGrant:
        """Read saved authority; execution callers also prove current lease ownership."""
        with self.store.connection() as connection:
            if owner is not None:
                self.store.require_lease(connection, run_id, owner)
            grant, _ = self._grant(connection, run_id, self.store.path)
            return grant

    def reserve(
        self,
        run_id: str,
        owner: str,
        call_id: str,
        arguments: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> CommandIntent:
        parsed = CommandArguments.model_validate(arguments)
        if not call_id or len(call_id) > 256 or not idempotency_key or len(idempotency_key) > 512:
            raise ValueError("Command intent requires bounded call and idempotency identities.")
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.store.require_lease(connection, run_id, owner)
            grant, workspace = self._grant(connection, run_id, self.store.path)
            run = connection.execute("SELECT state FROM runs WHERE id=?", (run_id,)).fetchone()
            if run[0] != "running":
                raise PermissionError("Commands may start only while their run is running.")
            parent_limit = connection.execute(
                "SELECT max_seconds FROM run_limits WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            if parsed.max_seconds > min(grant.max_seconds, parent_limit):
                raise PermissionError(
                    "Command duration exceeds its saved grant or parent allowance."
                )
            existing = connection.execute(
                "SELECT arguments,idempotency_key FROM tool_calls WHERE run_id=? AND (idempotency_key=? OR provider_call_id=?)",
                (run_id, idempotency_key, call_id),
            ).fetchone()
            if existing is not None:
                raise ReconciliationRequired(
                    "An action already owns this command call; never replay it."
                )
            active = connection.execute(
                "SELECT COUNT(*) FROM tool_calls WHERE tool_name=? AND state='intended'", (TOOL,)
            ).fetchone()[0]
            if active >= ACTIVE_COMMAND_LIMIT:
                raise CommandCapacityError(
                    "Two commands are active or awaiting recovery; reconcile them first."
                )
            intent = CommandIntent(
                **parsed.model_dump(),
                execution_id=uuid4().hex,
                backend=grant.backend,
                distribution=grant.distribution,
                profile_sha256=grant.profile_sha256,
                workspace=workspace,
            )
            now = utc_now()
            connection.execute(
                "INSERT INTO tool_calls (id,task_id,run_id,tool_name,tool_version,arguments,idempotency_key,state,result,error,created_at,updated_at,provider_call_id) VALUES (?,NULL,?,?,?, ?,?,'intended',NULL,NULL,?,?,?)",
                (
                    str(uuid4()),
                    run_id,
                    TOOL,
                    "1",
                    json.dumps(intent.model_dump(), sort_keys=True),
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
                {"call_id": call_id, "tool": TOOL, "internal": False},
            )
            self.store.record_event(
                connection,
                run_id,
                "execution.reserved",
                {
                    "call_id": call_id,
                    "execution_id": intent.execution_id,
                    "backend": intent.backend,
                    "profile_sha256": intent.profile_sha256,
                    "lease_binding_sha256": hashlib.sha256(owner.encode("utf-8")).hexdigest(),
                },
            )
        return intent

    @staticmethod
    def _intent(connection: Any, run_id: str, call_id: str) -> CommandIntent:
        row = connection.execute(
            "SELECT arguments,state,tool_name,tool_version FROM tool_calls WHERE run_id=? AND provider_call_id=?",
            (run_id, call_id),
        ).fetchone()
        if (
            row is None
            or row["state"] != "intended"
            or (row["tool_name"], row["tool_version"]) != (TOOL, "1")
        ):
            raise ReconciliationRequired("Command intent is missing or already resolved.")
        return CommandIntent.model_validate_json(row["arguments"])

    @staticmethod
    def _require_reservation_owner(connection: Any, run_id: str, call_id: str, owner: str) -> None:
        row = connection.execute(
            "SELECT json_extract(payload,'$.lease_binding_sha256') FROM run_events WHERE run_id=? AND event_type='execution.reserved' AND json_extract(payload,'$.call_id')=?",
            (run_id, call_id),
        ).fetchone()
        if row is None or row[0] != hashlib.sha256(owner.encode("utf-8")).hexdigest():
            raise ReconciliationRequired(
                "A new lease must reconcile the old command, never launch it."
            )

    def recovery_intent(self, run_id: str, owner: str, call_id: str) -> CommandIntent:
        """A replacement owner may inspect and close a saved identity, never relaunch it."""
        with self.store.connection() as connection:
            self.store.require_lease(connection, run_id, owner)
            intent = self._intent(connection, run_id, call_id)
            grant, workspace = self._grant(
                connection, run_id, self.store.path, validate_workspace=False
            )
            if (intent.profile_sha256, intent.distribution, intent.workspace) != (
                grant.profile_sha256,
                grant.distribution,
                workspace,
            ):
                raise PermissionError("Saved command recovery authority changed.")
            return intent

    def snapshot_intent(self, run_id: str, owner: str, call_id: str) -> CommandIntent:
        """Read capture authority without authorizing execution or accepting a caller path."""
        with self.store.connection() as connection:
            self.store.require_lease(connection, run_id, owner)
            intent = self._intent(connection, run_id, call_id)
            self._require_reservation_owner(connection, run_id, call_id, owner)
            grant, workspace = self._grant(connection, run_id, self.store.path)
            if (
                connection.execute("SELECT state FROM runs WHERE id=?", (run_id,)).fetchone()[0]
                != "running"
            ):
                raise PermissionError("Snapshot capture requires a running run.")
            if (intent.profile_sha256, intent.distribution, intent.workspace) != (
                grant.profile_sha256,
                grant.distribution,
                workspace,
            ):
                raise PermissionError("Saved snapshot authority changed.")
            if connection.execute(
                "SELECT 1 FROM run_events WHERE run_id=? AND event_type IN ('execution.snapshot','tool.executing') AND json_extract(payload,'$.call_id')=?",
                (run_id, call_id),
            ).fetchone():
                raise ReconciliationRequired(
                    "Snapshot or launch is already recorded; reconcile it."
                )
            return intent

    def snapshot_ready(
        self, run_id: str, owner: str, call_id: str, snapshot: WorkspaceSnapshot, artifact_id: str
    ) -> None:
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.store.require_lease(connection, run_id, owner)
            intent = self._intent(connection, run_id, call_id)
            self._require_reservation_owner(connection, run_id, call_id, owner)
            if connection.execute(
                "SELECT 1 FROM run_events WHERE run_id=? AND event_type='execution.snapshot' AND json_extract(payload,'$.call_id')=?",
                (run_id, call_id),
            ).fetchone():
                raise ReconciliationRequired("Command snapshot is already frozen.")
            artifact = connection.execute(
                "SELECT sha256,provenance FROM artifacts WHERE id=? AND run_id=?",
                (artifact_id, run_id),
            ).fetchone()
            if (
                artifact is None
                or artifact["sha256"] != snapshot.sha256
                or artifact["provenance"] != f"execution.snapshot:{intent.execution_id}"
            ):
                raise ValueError("Snapshot needs its exact registered manifest artifact.")
            self.store.record_event(
                connection,
                run_id,
                "execution.snapshot",
                {
                    "call_id": call_id,
                    "execution_id": intent.execution_id,
                    "sha256": snapshot.sha256,
                    "artifact_id": artifact_id,
                    "files": len(snapshot.files),
                    "bytes": sum(len(entry.content) for entry in snapshot.files),
                },
            )

    def authorize_launch(self, run_id: str, owner: str, call_id: str) -> CommandIntent:
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.store.require_lease(connection, run_id, owner)
            intent = self._intent(connection, run_id, call_id)
            self._require_reservation_owner(connection, run_id, call_id, owner)
            grant, workspace = self._grant(connection, run_id, self.store.path)
            if (
                connection.execute("SELECT state FROM runs WHERE id=?", (run_id,)).fetchone()[0]
                != "running"
            ):
                raise PermissionError("Command launch requires a running run.")
            if (intent.profile_sha256, intent.distribution, intent.workspace) != (
                grant.profile_sha256,
                grant.distribution,
                workspace,
            ):
                raise PermissionError("Saved command authority changed.")
            if not connection.execute(
                "SELECT 1 FROM run_events WHERE run_id=? AND event_type='execution.snapshot' AND json_extract(payload,'$.call_id')=? AND json_extract(payload,'$.execution_id')=?",
                (run_id, call_id, intent.execution_id),
            ).fetchone():
                raise ValueError("Freeze a registered workspace snapshot before launch.")
            if connection.execute(
                "SELECT 1 FROM run_events WHERE run_id=? AND event_type='tool.executing' AND json_extract(payload,'$.call_id')=?",
                (run_id, call_id),
            ).fetchone():
                raise ReconciliationRequired("Command launch was already authorized; reconcile it.")
            self.store.record_event(
                connection,
                run_id,
                "tool.executing",
                {"call_id": call_id, "execution_id": intent.execution_id},
            )
        return intent

    def complete(
        self, run_id: str, owner: str, call_id: str, outcome: CommandOutcome
    ) -> dict[str, Any]:
        outcome = CommandOutcome.model_validate(outcome.model_dump())
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.store.require_lease(connection, run_id, owner)
            intent = self._intent(connection, run_id, call_id)
            if outcome.execution_id != intent.execution_id:
                raise ValueError("Result belongs to another execution identity.")
            snapshot = connection.execute(
                "SELECT json_extract(payload,'$.sha256') FROM run_events WHERE run_id=? AND event_type='execution.snapshot' AND json_extract(payload,'$.call_id')=?",
                (run_id, call_id),
            ).fetchone()
            launched = connection.execute(
                "SELECT 1 FROM run_events WHERE run_id=? AND event_type='tool.executing' AND json_extract(payload,'$.call_id')=?",
                (run_id, call_id),
            ).fetchone()
            if snapshot is None or snapshot[0] != outcome.snapshot_sha256 or not launched:
                raise ValueError("Result lacks its exact authorized snapshot/launch.")
            result = outcome.model_dump()
            binding = connection.execute(
                "SELECT json_extract(payload,'$.lease_binding_sha256') FROM run_events WHERE run_id=? AND event_type='execution.reserved' AND json_extract(payload,'$.call_id')=?",
                (run_id, call_id),
            ).fetchone()
            result["result_reconciled"] = (
                binding is None or binding[0] != hashlib.sha256(owner.encode("utf-8")).hexdigest()
            )
            for stream in ("stdout", "stderr"):
                metadata = connection.execute(
                    "SELECT * FROM artifacts WHERE id=? AND run_id=?",
                    (result[stream + "_artifact_id"], run_id),
                ).fetchone()
                if metadata is None or (
                    metadata["sha256"],
                    metadata["size_bytes"],
                    metadata["provenance"],
                ) != (
                    result[stream + "_sha256"],
                    result[stream + "_bytes"],
                    f"execution.{stream}:{intent.execution_id}",
                ):
                    raise ValueError("Command output artifact identity or provenance mismatch.")
                path = self.store.path.parent / "artifacts" / metadata["storage_key"]
                root = command_artifact_root(self.store)
                if Path(metadata["storage_key"]).name != metadata[
                    "storage_key"
                ] or not path.resolve(strict=True).is_relative_to(root):
                    raise PermissionError("Command artifact escaped managed storage.")
                with open_for_read(path) as source:
                    if not opened_file_path(source).is_relative_to(root):
                        raise PermissionError("Opened command artifact escaped managed storage.")
                    data = source.read(1048577)
                if (
                    len(data) != result[stream + "_bytes"]
                    or hashlib.sha256(data).hexdigest() != result[stream + "_sha256"]
                ):
                    raise ValueError("Registered command output changed before completion.")
                preview = (
                    data.decode("utf-8", errors="replace")
                    .encode("utf-8")[:4096]
                    .decode("utf-8", errors="ignore")
                )
                result[stream + "_excerpt"] = preview
            result["trust"] = "untrusted_command_output"
            self.store.require_lease(connection, run_id, owner)
            connection.execute(
                "UPDATE tool_calls SET state='completed',result=?,updated_at=? WHERE run_id=? AND provider_call_id=? AND state='intended'",
                (json.dumps(result), utc_now(), run_id, call_id),
            )
            self.store.record_event(
                connection, run_id, "tool.completed", {"call_id": call_id, "internal": False}
            )
            self.store.record_event(
                connection,
                run_id,
                "execution.completed",
                {
                    "call_id": call_id,
                    "execution_id": intent.execution_id,
                    "succeeded": outcome.succeeded,
                    "cleanup_confirmed": True,
                    "result_reconciled": result["result_reconciled"],
                },
            )
        return result

    def close_interrupted(
        self, run_id: str, owner: str, call_id: str, evidence_artifact_id: str
    ) -> dict[str, Any]:
        """Release an interrupted slot after observed cleanup; never invent its result."""
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.store.require_lease(connection, run_id, owner)
            intent = self._intent(connection, run_id, call_id)
            artifact = connection.execute(
                "SELECT * FROM artifacts WHERE id=? AND run_id=?", (evidence_artifact_id, run_id)
            ).fetchone()
            if (
                artifact is None
                or artifact["provenance"] != f"execution.cleanup:{intent.execution_id}"
            ):
                raise ValueError("Recovery needs this execution's registered cleanup evidence.")
            root = command_artifact_root(self.store)
            key = artifact["storage_key"]
            if Path(key).name != key:
                raise PermissionError("Cleanup artifact escaped managed storage.")
            with open_for_read(root / key) as source:
                if not opened_file_path(source).is_relative_to(root):
                    raise PermissionError("Opened cleanup evidence escaped managed storage.")
                data = source.read(65537)
            if (
                len(data) > 65536
                or len(data) != artifact["size_bytes"]
                or hashlib.sha256(data).hexdigest() != artifact["sha256"]
            ):
                raise ValueError("Cleanup evidence changed or exceeded its limit.")
            proof = CommandCleanupEvidence.model_validate_json(data)
            if (proof.execution_id, proof.distribution, proof.profile_sha256) != (
                intent.execution_id,
                intent.distribution,
                intent.profile_sha256,
            ):
                raise ValueError("Cleanup evidence belongs to a different execution or profile.")
            result = {
                "ok": False,
                "execution_id": intent.execution_id,
                "verification": "not_verified",
                "cleanup_confirmed": True,
                "manager_outcome_available": proof.manager_outcome_available,
                "evidence_artifact_id": evidence_artifact_id,
                "error": {
                    "code": "command_result_unavailable_locally",
                    "message": "The private command was sealed and cleaned up. Its complete result was not recorded; it has not been replayed.",
                },
            }
            self.store.require_lease(connection, run_id, owner)
            connection.execute(
                "UPDATE tool_calls SET state='failed',result=?,error=?,updated_at=? WHERE run_id=? AND provider_call_id=? AND state='intended'",
                (json.dumps(result), json.dumps(result["error"]), utc_now(), run_id, call_id),
            )
            self.store.record_event(
                connection,
                run_id,
                "tool.failed",
                {
                    "call_id": call_id,
                    "internal": False,
                    "effect": "interrupted_private_execution",
                    "code": "command_result_unavailable_locally",
                },
            )
            self.store.record_event(
                connection,
                run_id,
                "execution.reconciled",
                {
                    "call_id": call_id,
                    "execution_id": intent.execution_id,
                    "verified": False,
                    "cleanup_confirmed": True,
                    "evidence_artifact_id": evidence_artifact_id,
                },
            )
        return result

    def record_stopped_cleanup(
        self,
        run_id: str,
        expected_revision: int,
        call_id: str,
        proof: CommandCleanupEvidence,
        *,
        preserve_available: bool = False,
    ) -> dict[str, Any]:
        """Application cleanup after user stop revoked the worker lease.

        This is not a model operation or an endpoint accepting client assertions.
        The controller must obtain proof from the known backend, then use the
        exact stopped revision. Only an unverified failure can be recorded here.
        """
        proof = CommandCleanupEvidence.model_validate(proof.model_dump())
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("Cleanup needs the exact stopped run revision.")
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT state,revision,lease_owner FROM runs WHERE id=?", (run_id,)
            ).fetchone()
            if (
                run is None
                or run["revision"] != expected_revision
                or run["state"] not in {"paused", "blocked", "failed", "cancelled"}
                or run["lease_owner"] is not None
            ):
                raise ReconciliationRequired(
                    "Stopped cleanup became stale; reconcile the current run."
                )
            intent = self._intent(connection, run_id, call_id)
            if (proof.execution_id, proof.distribution, proof.profile_sha256) != (
                intent.execution_id,
                intent.distribution,
                intent.profile_sha256,
            ):
                raise ValueError("Cleanup observation belongs to a different execution.")
            data = proof.model_dump_json().encode("utf-8")
            directory = command_artifact_root(self.store, create=True)
            filename = f"{uuid4()}.command-cleanup.json"
            with (directory / filename).open("xb") as target:
                target.write(data)
                target.flush()
                os.fsync(target.fileno())
            artifact_id = str(uuid4())
            connection.execute(
                "INSERT INTO artifacts VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?)",
                (
                    artifact_id,
                    run_id,
                    "application/json",
                    filename,
                    len(data),
                    hashlib.sha256(data).hexdigest(),
                    f"execution.cleanup:{intent.execution_id}",
                    utc_now(),
                ),
            )
            if preserve_available and proof.manager_outcome_available:
                observation = {
                    "call_id": call_id,
                    "execution_id": intent.execution_id,
                    "cleanup_confirmed": True,
                    "manager_outcome_available": True,
                    "evidence_artifact_id": artifact_id,
                    "stopped_revision": expected_revision,
                    "verification": "pending_local_result_review",
                    "actor": "application_cleanup",
                }
                self.store.record_event(
                    connection, run_id, "execution.cleanup_observed", observation
                )
                return observation
            result = {
                "ok": False,
                "execution_id": intent.execution_id,
                "verification": "not_verified",
                "cleanup_confirmed": True,
                "evidence_artifact_id": artifact_id,
                "manager_outcome_available": proof.manager_outcome_available,
                "error": {
                    "code": "command_result_unavailable_locally",
                    "message": "Stopped command was sealed and cleaned up; no command result was verified or replayed.",
                },
            }
            connection.execute(
                "UPDATE tool_calls SET state='failed',result=?,error=?,updated_at=? WHERE run_id=? AND provider_call_id=? AND state='intended'",
                (json.dumps(result), json.dumps(result["error"]), utc_now(), run_id, call_id),
            )
            self.store.record_event(
                connection,
                run_id,
                "tool.failed",
                {
                    "call_id": call_id,
                    "internal": False,
                    "effect": "interrupted_private_execution",
                    "code": "command_result_unavailable_locally",
                },
            )
            self.store.record_event(
                connection,
                run_id,
                "execution.reconciled",
                {
                    "call_id": call_id,
                    "execution_id": intent.execution_id,
                    "verified": False,
                    "cleanup_confirmed": True,
                    "evidence_artifact_id": artifact_id,
                    "actor": "application_cleanup",
                    "stopped_revision": expected_revision,
                },
            )
        return result
