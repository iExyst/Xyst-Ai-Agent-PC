"""SQLite-backed runs and durable ordered events."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from xaa.providers.contracts import public_assistant_message, public_run_event
from xaa.runtime.file_handles import opened_file_path
from xaa.runtime.states import ALLOWED_TRANSITIONS, RunState
from xaa.storage.conversation_index import install_index

DATABASE_SCHEMA_VERSION = 11


@dataclass(frozen=True)
class TaskReadScope:
    run_id: str
    parent_token: str
    task_id: str
    task_token: str


class InvalidTransition(ValueError):
    """Raised when a requested run transition is not allowed."""


class LeaseLost(RuntimeError):
    """Worker no longer owns a live lease and must stop persisting results."""


class TokenBudgetExceeded(RuntimeError):
    """Measured token threshold reached or usage cannot be established."""


class IterationBudgetExceeded(RuntimeError):
    """The shared run allowance was exhausted before an inference attempt."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def default_database_path() -> Path:
    root = os.environ.get("XAA_DATA_DIR") or os.environ.get("LOCALAPPDATA")
    if root:
        return Path(root) / "XAA" / "xaa.sqlite3"
    return Path.home() / ".xaa" / "xaa.sqlite3"


class RunStore:
    @staticmethod
    def count_inference_attempts(connection: sqlite3.Connection, run_id: str) -> int:
        return connection.execute(
            "SELECT COUNT(*) FROM run_events WHERE run_id=? AND (event_type IN ('model.inference.started','task.inference.started') OR (event_type IN ('model.message','model.incomplete') AND json_extract(payload,'$.inference_id') IS NULL))",
            (run_id,),
        ).fetchone()[0]

    def inference_attempts(self, run_id: str) -> int:
        with self.connection() as connection:
            return self.count_inference_attempts(connection, run_id)

    def reserve_parent_inference(self, run_id: str, token: str, *, limit: int) -> str:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.require_lease(connection, run_id, token)
            saved_limit = connection.execute(
                "SELECT max_iterations FROM run_limits WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            if self.count_inference_attempts(connection, run_id) >= min(limit, saved_limit):
                raise IterationBudgetExceeded("Shared iteration budget exhausted.")
            identity = str(uuid.uuid4())
            self.record_event(
                connection, run_id, "model.inference.started", {"inference_id": identity}
            )
            return identity

    def register_artifact(
        self,
        run_id: str,
        token: str,
        filename: str,
        *,
        provenance: str,
        media_type: str = "text/plain",
    ) -> dict[str, Any]:
        if media_type not in {"text/plain", "image/png"}:
            raise ValueError("Managed artifacts support only plain text and PNG images.")
        root = (self.path.parent / "artifacts").resolve(strict=True)
        if Path(filename).name != filename:
            raise ValueError("Artifact name must not contain a path.")
        artifact = (root / filename).resolve(strict=True)
        if not artifact.is_relative_to(root):
            raise PermissionError("Artifact escaped managed storage.")
        with artifact.open("rb") as source:
            if not opened_file_path(source).is_relative_to(root):
                raise PermissionError("Opened artifact escaped managed storage.")
            content = source.read(8 * 1024 * 1024 + 1)
        size = len(content)
        if size > 8 * 1024 * 1024:
            raise ValueError("Artifact exceeds the current storage limit.")
        identity = str(uuid.uuid4())
        digest = hashlib.sha256(content).hexdigest()
        created = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.require_lease(connection, run_id, token)
            connection.execute(
                "INSERT INTO artifacts VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?)",
                (identity, run_id, media_type, filename, size, digest, provenance, created),
            )
            self.record_event(
                connection,
                run_id,
                "artifact.created",
                {"artifact_id": identity, "size_bytes": size},
            )
        return {
            "id": identity,
            "run_id": run_id,
            "media_type": media_type,
            "size_bytes": size,
            "sha256": digest,
            "provenance": provenance,
            "created_at": created,
        }

    def list_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, run_id, media_type, size_bytes, sha256, provenance, created_at FROM artifacts WHERE run_id = ? ORDER BY created_at",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def __init__(
        self,
        path: Path | None = None,
        *,
        task_scope: TaskReadScope | None = None,
        internal_tools: bool = False,
    ) -> None:
        self.task_scope = task_scope
        self.internal_tools = internal_tools
        self.path = path or default_database_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            if connection.execute("PRAGMA user_version").fetchone()[0] > DATABASE_SCHEMA_VERSION:
                raise RuntimeError(
                    "Database schema is newer than this engine; upgrade the engine before opening it."
                )
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            current_run_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(runs)").fetchall()
            }
            if current_run_columns and "lease_owner" not in current_run_columns:
                connection.execute("ALTER TABLE runs ADD COLUMN lease_owner TEXT")
                connection.execute("ALTER TABLE runs ADD COLUMN lease_expires_at TEXT")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    objective TEXT NOT NULL,
                    acceptance_criteria TEXT NOT NULL,
                    workspace TEXT,
                    mode TEXT NOT NULL,
                    model TEXT,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_expires_at TEXT
                );
                CREATE INDEX IF NOT EXISTS runs_state_updated ON runs(state, updated_at);
                CREATE TABLE IF NOT EXISTS run_limits (
                    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
                    max_iterations INTEGER NOT NULL CHECK(max_iterations BETWEEN 1 AND 100),
                    max_seconds INTEGER NOT NULL CHECK(max_seconds BETWEEN 1 AND 86400)
                );
                CREATE TABLE IF NOT EXISTS run_checks (
                    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
                    checks TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS run_events (
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    channel TEXT NOT NULL,
                    account_id TEXT,
                    title TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    schema_version INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
                    sequence INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(session_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    parent_task_id TEXT REFERENCES tasks(id) ON DELETE CASCADE,
                    objective TEXT NOT NULL,
                    acceptance_criteria TEXT NOT NULL,
                    assigned_role TEXT NOT NULL,
                    model_route TEXT,
                    workspace TEXT,
                    permission_scope TEXT NOT NULL,
                    budgets TEXT NOT NULL,
                    status TEXT NOT NULL,
                    checkpoint TEXT,
                    result TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    schema_version INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS task_dependencies (
                    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    depends_on_task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    PRIMARY KEY(task_id, depends_on_task_id),
                    CHECK(task_id != depends_on_task_id)
                );
                CREATE TABLE IF NOT EXISTS task_leases (
                    task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
                    token TEXT NOT NULL UNIQUE,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tool_calls (
                    id TEXT PRIMARY KEY,
                    task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    tool_name TEXT NOT NULL,
                    tool_version TEXT NOT NULL,
                    arguments TEXT NOT NULL,
                    idempotency_key TEXT,
                    state TEXT NOT NULL,
                    result TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(run_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
                    media_type TEXT NOT NULL,
                    storage_key TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
                    sha256 TEXT NOT NULL,
                    provenance TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    tool_call_id TEXT REFERENCES tool_calls(id) ON DELETE CASCADE,
                    requested_scope TEXT NOT NULL,
                    action_summary TEXT NOT NULL,
                    decision TEXT,
                    decided_at TEXT,
                    expires_at TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    content TEXT NOT NULL,
                    provenance TEXT NOT NULL,
                    confidence REAL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    schema_version INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS skills (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    lifecycle_state TEXT NOT NULL,
                    active_version INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS skill_versions (
                    skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
                    version INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    required_tools TEXT NOT NULL,
                    required_permissions TEXT NOT NULL,
                    evaluation_evidence TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(skill_id, version)
                );
                CREATE TABLE IF NOT EXISTS skill_lifecycle_events (
                    skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(skill_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS schedules (
                    id TEXT PRIMARY KEY,
                    objective TEXT NOT NULL,
                    recurrence TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    missed_run_policy TEXT NOT NULL,
                    overlap_policy TEXT NOT NULL,
                    quiet_hours TEXT,
                    delivery_destination TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS schedule_executions (
                    id TEXT PRIMARY KEY,
                    schedule_id TEXT NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
                    occurrence_id TEXT NOT NULL UNIQUE,
                    run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
                    state TEXT NOT NULL,
                    delivery_evidence TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS schedule_deliveries (
                    execution_id TEXT PRIMARY KEY REFERENCES schedule_executions(id) ON DELETE CASCADE,
                    account_id TEXT NOT NULL,
                    external_identity TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    message_id INTEGER,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS schedule_deliveries_pending
                    ON schedule_deliveries(account_id,state,created_at);
                CREATE TABLE IF NOT EXISTS channel_bindings (
                    id TEXT PRIMARY KEY,
                    channel TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    external_identity TEXT NOT NULL,
                    session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
                    identity_link_id TEXT,
                    authorized INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    UNIQUE(channel, account_id, external_identity)
                );
                CREATE TABLE IF NOT EXISTS telegram_pairing_codes (
                    code_hash TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS telegram_bots (
                    account_id TEXT PRIMARY KEY,
                    username TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS telegram_updates (
                    account_id TEXT NOT NULL,
                    update_id INTEGER NOT NULL,
                    user_id TEXT,
                    chat_id TEXT,
                    session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
                    run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
                    state TEXT NOT NULL,
                    delivery_state TEXT,
                    delivery_message_id INTEGER,
                    received_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(account_id, update_id)
                );
                CREATE INDEX IF NOT EXISTS telegram_updates_delivery
                    ON telegram_updates(delivery_state, updated_at);
                CREATE TABLE IF NOT EXISTS telegram_offsets (
                    account_id TEXT PRIMARY KEY,
                    next_offset INTEGER NOT NULL,
                    last_update_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS provider_profiles (
                    id TEXT PRIMARY KEY,
                    adapter TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    selected_model TEXT,
                    secret_reference TEXT,
                    capability_snapshot TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mcp_servers (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    command TEXT NOT NULL,
                    args TEXT NOT NULL,
                    cwd TEXT,
                    secret_env TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mcp_server_tools (
                    server_id TEXT NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    title TEXT,
                    description TEXT,
                    input_schema TEXT NOT NULL,
                    output_schema TEXT,
                    schema_sha256 TEXT NOT NULL,
                    permission TEXT NOT NULL CHECK(permission IN ('none','read','write')),
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(server_id,name)
                );
                """
            )
            connection.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(tool_calls)")}
            if "provider_call_id" not in columns:
                connection.execute("ALTER TABLE tool_calls ADD COLUMN provider_call_id TEXT")
            # Legacy journal IDs were the provider IDs. Keep their primary keys and FKs intact.
            connection.execute(
                "UPDATE tool_calls SET provider_call_id=id WHERE provider_call_id IS NULL"
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS tool_calls_run_provider_idx ON tool_calls(run_id,provider_call_id)"
            )
            delivery_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(schedule_deliveries)")
            }
            if "attempt_count" not in delivery_columns:
                connection.execute(
                    "ALTER TABLE schedule_deliveries ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0"
                )
            if "next_attempt_at" not in delivery_columns:
                connection.execute(
                    "ALTER TABLE schedule_deliveries ADD COLUMN next_attempt_at TEXT"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS schedule_deliveries_retry ON schedule_deliveries(account_id,state,next_attempt_at,created_at)"
            )
            if connection.execute("PRAGMA user_version").fetchone()[0] < 3:
                install_index(connection)
            connection.execute(f"PRAGMA user_version={DATABASE_SCHEMA_VERSION}")

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection]:
        """Share the database transaction boundary with cohesive storage modules."""
        with self._connection() as connection:
            yield connection

    def backup(self) -> Path:
        """Create an integrity-checked consistent snapshot using SQLite's online backup API."""
        directory = self.path.parent / "backups"
        directory.mkdir(exist_ok=True)
        destination = directory / f"{uuid.uuid4()}.sqlite3"
        temporary = destination.with_suffix(".partial")
        target = sqlite3.connect(temporary)
        try:
            with self._connection() as source:
                source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity verification failed.")
            if target.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise RuntimeError("Backup contains invalid foreign-key references.")
        except BaseException:
            target.close()
            temporary.unlink(missing_ok=True)
            raise
        else:
            target.close()
            temporary.replace(destination)
        return destination

    @staticmethod
    def record_event(
        connection: sqlite3.Connection, run_id: str, kind: str, payload: dict[str, Any]
    ) -> None:
        if kind in {"model.message", "task.model.message"} and isinstance(
            payload.get("message"), dict
        ):
            payload = {**payload, "message": public_assistant_message(payload["message"])}
        sequence = connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM run_events WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO run_events VALUES (?, ?, ?, ?, ?)",
            (run_id, sequence, kind, json.dumps(payload), utc_now()),
        )

    def create_run(
        self,
        *,
        objective: str,
        acceptance_criteria: list[str],
        workspace: str | None,
        mode: str,
        model: str | None,
        max_iterations: int = 8,
        max_seconds: int = 900,
        max_total_tokens: int | None = None,
        max_output_tokens: int | None = None,
        file_checks: list[dict[str, Any]] | None = None,
        procedure_trial: dict[str, Any] | None = None,
        command_grant: dict[str, Any] | None = None,
        approved_web_hosts: list[str] | None = None,
        approved_web_search: bool = False,
        approved_browser: bool = False,
        approved_mcp_tools: list[str] | None = None,
    ) -> dict[str, Any]:
        if max_total_tokens is not None and (
            type(max_total_tokens) is not int or not 1 <= max_total_tokens <= 100_000_000
        ):
            raise ValueError("Token threshold must be an integer from 1 to 100000000.")
        if max_output_tokens is not None and (
            type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 1_000_000
        ):
            raise ValueError("Output token limit must be an integer from 1 to 1000000.")
        if approved_web_hosts:
            from xaa.runtime.web_fetch import normalize_approved_hosts

            approved_web_hosts = normalize_approved_hosts(approved_web_hosts)
        approved_mcp_tools = sorted(set(approved_mcp_tools or []))
        if len(approved_mcp_tools) > 50 or any(
            re.fullmatch(r"mcp_[a-f0-9]{32}_[A-Za-z0-9_-]{1,24}", name) is None
            for name in approved_mcp_tools
        ):
            raise ValueError("Approve up to 50 valid MCP tool names per run.")
        granted_command = None
        if command_grant is not None:
            from xaa.runtime.command_contracts import CommandGrant, command_workspace

            grant = CommandGrant.model_validate(command_grant)
            selected = command_workspace(workspace, self.path, mode)
            granted_command = {
                "grant": grant.model_dump(),
                "workspace": str(selected),
                "actor": "local_user",
            }
        run_id = str(uuid.uuid4())
        now = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            trial_snapshot = None
            if procedure_trial is not None:
                from xaa.storage.skills import SkillTrial, resolve_trial

                trial_snapshot = resolve_trial(
                    connection,
                    SkillTrial.model_validate(procedure_trial),
                    mode=mode,
                    workspace=workspace,
                )
            connection.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL)",
                (
                    run_id,
                    objective,
                    json.dumps(acceptance_criteria),
                    workspace,
                    mode,
                    model,
                    RunState.QUEUED.value,
                    now,
                    now,
                ),
            )
            self.record_event(
                connection,
                run_id,
                "run.queued",
                {
                    "state": RunState.QUEUED.value,
                    "max_total_tokens": max_total_tokens,
                    "max_output_tokens": max_output_tokens,
                },
            )
            if trial_snapshot is not None:
                self.record_event(connection, run_id, "procedure.selected", trial_snapshot)
            if granted_command is not None:
                self.record_event(connection, run_id, "execution.granted", granted_command)
            if approved_web_hosts or approved_web_search or approved_browser:
                self.record_event(
                    connection,
                    run_id,
                    "web.access_granted",
                    {
                        "hosts": approved_web_hosts,
                        "search": approved_web_search,
                        "browser": approved_browser,
                        "actor": "local_user",
                    },
                )
            if approved_mcp_tools:
                self.record_event(
                    connection,
                    run_id,
                    "mcp.access_granted",
                    {"tools": approved_mcp_tools, "actor": "local_user"},
                )
            connection.execute(
                "INSERT INTO run_limits VALUES (?, ?, ?)", (run_id, max_iterations, max_seconds)
            )
            connection.execute(
                "INSERT INTO run_checks VALUES (?, ?)", (run_id, json.dumps(file_checks or []))
            )
        result = self.get_run(run_id)
        assert result is not None
        return result

    def procedure_trial(self, run_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT payload FROM run_events WHERE run_id=? AND event_type='procedure.selected' ORDER BY sequence LIMIT 1",
                (run_id,),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        if self.task_scope is not None:
            with self._connection() as connection:
                self.require_lease(connection, run_id, self.task_scope.parent_token)
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        record: dict[str, Any] = dict(row)
        with self._connection() as connection:
            grants = connection.execute(
                "SELECT payload FROM run_events WHERE run_id=? AND event_type='execution.granted'",
                (run_id,),
            ).fetchall()
        record["command_grant"] = json.loads(grants[0][0])["grant"] if len(grants) == 1 else None
        with self._connection() as connection:
            web_grants = connection.execute(
                "SELECT payload FROM run_events WHERE run_id=? AND event_type='web.access_granted'",
                (run_id,),
            ).fetchall()
        record["approved_web_hosts"] = (
            json.loads(web_grants[0][0])["hosts"] if len(web_grants) == 1 else []
        )
        record["approved_web_search"] = (
            bool(json.loads(web_grants[0][0]).get("search", False))
            if len(web_grants) == 1
            else False
        )
        record["approved_browser"] = (
            bool(json.loads(web_grants[0][0]).get("browser", False))
            if len(web_grants) == 1
            else False
        )
        with self._connection() as connection:
            mcp_grants = connection.execute(
                "SELECT payload FROM run_events WHERE run_id=? AND event_type='mcp.access_granted'",
                (run_id,),
            ).fetchall()
        record["approved_mcp_tools"] = (
            json.loads(mcp_grants[0][0]).get("tools", []) if len(mcp_grants) == 1 else []
        )
        trial = self.procedure_trial(run_id)
        record["procedure_trial"] = (
            {
                key: trial[key]
                for key in ("skill_id", "version", "name", "snapshot_sha256", "purpose")
            }
            if trial
            else None
        )
        record["acceptance_criteria"] = json.loads(record["acceptance_criteria"])
        with self._connection() as connection:
            limits = connection.execute(
                "SELECT max_iterations, max_seconds FROM run_limits WHERE run_id = ?", (run_id,)
            ).fetchone()
        run_limits: dict[str, Any] = (
            dict(limits) if limits is not None else {"max_iterations": 8, "max_seconds": 900}
        )
        with self._connection() as connection:
            configured = connection.execute(
                "SELECT json_extract(payload,'$.max_total_tokens') FROM run_events WHERE run_id=? AND event_type='run.queued' ORDER BY sequence LIMIT 1",
                (run_id,),
            ).fetchone()
        run_limits["max_total_tokens"] = configured[0] if configured else None
        with self._connection() as connection:
            output = connection.execute(
                "SELECT json_extract(payload,'$.max_output_tokens') FROM run_events WHERE run_id=? AND event_type='run.queued' ORDER BY sequence LIMIT 1",
                (run_id,),
            ).fetchone()
        run_limits["max_output_tokens"] = output[0] if output else None
        record["limits"] = run_limits
        with self._connection() as connection:
            checks = connection.execute(
                "SELECT checks FROM run_checks WHERE run_id = ?", (run_id,)
            ).fetchone()
        record["file_checks"] = json.loads(checks["checks"]) if checks is not None else []
        if self.task_scope is not None:
            record["mode"] = "observe"
            record["command_grant"] = None
        return record

    def acquire_lease(self, run_id: str, owner: str, *, seconds: int = 30) -> str | None:
        """Acquire a new ownership period; return its unique fencing token."""
        if not owner or seconds < 1 or seconds > 3600:
            raise ValueError("Lease requires an owner and duration between 1 and 3600 seconds.")
        now = datetime.now(UTC)
        expires = (now + timedelta(seconds=seconds)).isoformat()
        token = str(uuid.uuid4())
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                "UPDATE runs SET lease_owner = ?, lease_expires_at = ? "
                "WHERE id = ? AND state IN ('queued', 'planning', 'running', 'verifying') "
                "AND (lease_owner IS NULL OR lease_expires_at <= ?)",
                (token, expires, run_id, now.isoformat()),
            ).rowcount
            if changed:
                self.record_event(
                    connection,
                    run_id,
                    "run.lease_acquired",
                    {"owner": owner, "expires_at": expires},
                )
            return token if changed == 1 else None

    def renew_lease(self, run_id: str, token: str, *, seconds: int = 30) -> None:
        """Extend a live ownership period without changing its fencing token."""
        if not 1 <= seconds <= 3600:
            raise ValueError("Lease duration must be between 1 and 3600 seconds.")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.require_lease(connection, run_id, token)
            connection.execute(
                "UPDATE runs SET lease_expires_at = ? WHERE id = ?",
                ((datetime.now(UTC) + timedelta(seconds=seconds)).isoformat(), run_id),
            )

    def release_lease(self, run_id: str, owner: str) -> bool:
        """Release a lease only if the caller owns it."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                "UPDATE runs SET lease_owner = NULL, lease_expires_at = NULL "
                "WHERE id = ? AND lease_owner = ?",
                (run_id, owner),
            ).rowcount
            if changed:
                self.record_event(connection, run_id, "run.lease_released", {"owner": owner})
            return changed == 1

    def _stop_specialists(
        self, connection: sqlite3.Connection, run_id: str, reason: str, *, cancel: bool = False
    ) -> int:
        states = ("queued", "running", "verifying") if cancel else ("running",)
        placeholders = ",".join("?" for _ in states)
        rows = connection.execute(
            f"SELECT id FROM tasks WHERE run_id=? AND status IN ({placeholders})",
            (run_id, *states),
        ).fetchall()
        destination = "cancelled" if cancel else "blocked"
        for row in rows:
            identity = row["id"]
            connection.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                (destination, utc_now(), identity),
            )
            connection.execute("DELETE FROM task_leases WHERE task_id=?", (identity,))
            self.record_event(
                connection,
                run_id,
                f"task.{destination}",
                {
                    "task_id": identity[len(run_id) + 1 :],
                    "reason": reason,
                },
            )
        return len(rows)

    def transition(
        self,
        run_id: str,
        destination: RunState,
        *,
        reason: str | None = None,
        owner: str | None = None,
    ) -> dict[str, Any]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if owner is not None:
                self.require_lease(connection, run_id, owner)
            row = connection.execute("SELECT state FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            source = RunState(row["state"])
            if destination not in ALLOWED_TRANSITIONS[source]:
                raise InvalidTransition(
                    f"Cannot transition run from {source.value} to {destination.value}."
                )
            now = utc_now()
            connection.execute(
                "UPDATE runs SET state = ?, updated_at = ?, revision = revision + 1 WHERE id = ? AND state = ?",
                (destination.value, now, run_id, source.value),
            )
            if destination in {
                RunState.PAUSED,
                RunState.BLOCKED,
                RunState.FAILED,
                RunState.CANCELLED,
                RunState.WAITING_FOR_APPROVAL,
            }:
                self._stop_specialists(
                    connection,
                    run_id,
                    f"parent_{destination.value}",
                    cancel=destination == RunState.CANCELLED,
                )
                connection.execute(
                    "UPDATE runs SET lease_owner=NULL,lease_expires_at=NULL WHERE id=?", (run_id,)
                )
            if destination == RunState.QUEUED:
                connection.execute(
                    "UPDATE runs SET lease_owner=NULL,lease_expires_at=NULL WHERE id=?", (run_id,)
                )
            self.record_event(
                connection,
                run_id,
                "run.state_changed",
                {"from": source.value, "to": destination.value, "reason": reason},
            )
        result = self.get_run(run_id)
        assert result is not None
        return result

    def require_lease(self, connection: sqlite3.Connection, run_id: str, owner: str) -> None:
        row = connection.execute(
            "SELECT lease_owner, lease_expires_at, state FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        if (
            row is None
            or row["lease_owner"] != owner
            or not row["lease_expires_at"]
            or row["lease_expires_at"] <= utc_now()
            or row["state"] not in {"queued", "planning", "running", "verifying"}
        ):
            raise LeaseLost("Run lease expired, changed owner, or execution was stopped.")
        if self.task_scope is not None:
            scope = self.task_scope
            if run_id != scope.run_id or owner != scope.parent_token:
                raise PermissionError("Task reader cannot use another run or parent lease.")
            self.require_task_read_scope(connection, run_id, scope.task_id, scope.task_token)

    @staticmethod
    def require_task_read_scope(
        connection: sqlite3.Connection, run_id: str, task_id: str, task_token: str
    ) -> None:
        row = connection.execute(
            "SELECT t.permission_scope,t.workspace,r.workspace AS parent_workspace "
            "FROM tasks t JOIN task_leases l ON l.task_id=t.id JOIN runs r ON r.id=t.run_id "
            "WHERE t.id=? AND t.run_id=? AND t.status='running' AND l.token=? AND l.expires_at>?",
            (f"{run_id}:{task_id}", run_id, task_token, utc_now()),
        ).fetchone()
        if (
            row is None
            or "workspace.read" not in json.loads(row["permission_scope"])
            or row["workspace"] != row["parent_workspace"]
        ):
            raise PermissionError("Task reader no longer holds its saved workspace/read authority.")

    def append_worker_event(
        self, run_id: str, owner: str, kind: str, payload: dict[str, Any]
    ) -> None:
        """Fence worker output with the lease check in the same write transaction."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.require_lease(connection, run_id, owner)
            self.record_event(connection, run_id, kind, payload)

    def list_events(self, run_id: str, after_sequence: int = 0) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT sequence, event_type, payload, created_at FROM run_events "
                "WHERE run_id = ? AND sequence > ? ORDER BY sequence",
                (run_id, after_sequence),
            ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def public_events(self, run_id: str, after_sequence: int = 0) -> list[dict[str, Any]]:
        return [public_run_event(event) for event in self.list_events(run_id, after_sequence)]

    def require_new_tool_call(self, run_id: str, token: str, call_id: str) -> None:
        """Reject reused provider IDs before dispatch; intent insertion remains atomic."""
        with self.connection() as connection:
            self.require_lease(connection, run_id, token)
            if connection.execute(
                "SELECT 1 FROM tool_calls WHERE run_id=? AND provider_call_id=?", (run_id, call_id)
            ).fetchone():
                raise ValueError("Tool call ID already belongs to another action in this run.")

    def record_tool_intent(
        self,
        run_id: str,
        token: str,
        *,
        call_id: str,
        tool: str,
        version: str,
        arguments: dict[str, Any],
        idempotency_key: str,
        internal: bool = False,
    ) -> bool:
        """Persist before execution. False means reconcile the existing call, never replay it blindly."""
        if not call_id or not idempotency_key:
            raise ValueError("Tool intent requires a call ID and idempotency key.")
        encoded = json.dumps(arguments, sort_keys=True)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.require_lease(connection, run_id, token)
            existing = connection.execute(
                "SELECT * FROM tool_calls WHERE run_id = ? AND idempotency_key = ?",
                (run_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if (existing["tool_name"], existing["tool_version"], existing["arguments"]) != (
                    tool,
                    version,
                    encoded,
                ):
                    raise ValueError("Idempotency key already belongs to a different action.")
                return False
            now = utc_now()
            if connection.execute(
                "SELECT 1 FROM tool_calls WHERE run_id=? AND provider_call_id=?", (run_id, call_id)
            ).fetchone():
                raise ValueError("Tool call ID already belongs to another action in this run.")
            connection.execute(
                "INSERT INTO tool_calls (id,task_id,run_id,tool_name,tool_version,arguments,idempotency_key,state,result,error,created_at,updated_at,provider_call_id) VALUES (?, NULL, ?, ?, ?, ?, ?, 'intended', NULL, NULL, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    run_id,
                    tool,
                    version,
                    encoded,
                    idempotency_key,
                    now,
                    now,
                    call_id,
                ),
            )
            self.record_event(
                connection,
                run_id,
                "tool.intended",
                {"call_id": call_id, "tool": tool, "internal": internal or self.internal_tools},
            )
            return True

    def record_tool_result(
        self,
        run_id: str,
        token: str,
        call_id: str,
        result: dict[str, Any],
        *,
        internal: bool = False,
    ) -> None:
        """Commit result and its event together while the worker still holds its lease."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.require_lease(connection, run_id, token)
            internal = (
                internal
                or self.internal_tools
                or connection.execute(
                    "SELECT 1 FROM run_events WHERE run_id=? AND event_type='tool.intended' "
                    "AND json_extract(payload,'$.call_id')=? AND json_extract(payload,'$.internal')=1",
                    (run_id, call_id),
                ).fetchone()
                is not None
            )
            changed = connection.execute(
                "UPDATE tool_calls SET state = 'completed', result = ?, updated_at = ? "
                "WHERE provider_call_id = ? AND run_id = ? AND state = 'intended'",
                (json.dumps(result), utc_now(), call_id, run_id),
            ).rowcount
            if changed != 1:
                raise ValueError("Tool call is missing or already resolved.")
            self.record_event(
                connection, run_id, "tool.completed", {"call_id": call_id, "internal": internal}
            )

    def record_tool_failure(
        self, run_id: str, token: str, call_id: str, *, code: str, message: str
    ) -> dict[str, Any]:
        """Resolve a known pre-effect failure; never use for an ambiguous external effect."""
        result: dict[str, Any] = {
            "ok": False,
            "effect": "not_started",
            "error": {"code": code, "message": message},
        }
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.require_lease(connection, run_id, token)
            if connection.execute(
                "SELECT 1 FROM run_events WHERE run_id=? AND event_type='tool.executing' AND json_extract(payload,'$.call_id')=? LIMIT 1",
                (run_id, call_id),
            ).fetchone():
                raise ValueError("Effect may have started; destination reconciliation is required.")
            changed = connection.execute(
                "UPDATE tool_calls SET state='failed',result=?,error=?,updated_at=? WHERE run_id=? AND provider_call_id=? AND state='intended'",
                (json.dumps(result), json.dumps(result["error"]), utc_now(), run_id, call_id),
            ).rowcount
            if changed != 1:
                raise ValueError("Tool call is missing or already resolved.")
            self.record_event(
                connection,
                run_id,
                "tool.failed",
                {
                    "call_id": call_id,
                    "internal": self.internal_tools,
                    "effect": "not_started",
                    "code": code,
                },
            )
        return result

    def resolve_interrupted_reads(self, run_id: str, token: str) -> list[str]:
        """Close read-only intents without replaying them or assuming any write failed."""
        names = {
            "workspace.read_file": "workspace_read_file",
            "workspace.list_directory": "workspace_list_directory",
            "workspace.search": "workspace_search",
        }
        resolved: list[str] = []
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.require_lease(connection, run_id, token)
            proposals: dict[str, str] = {}
            rows = connection.execute(
                "SELECT provider_call_id,tool_name,tool_version FROM tool_calls WHERE run_id=? AND state='intended' AND tool_version='1' AND tool_name IN ('workspace.read_file','workspace.list_directory','workspace.search')",
                (run_id,),
            ).fetchall()
            if not rows:
                return []
            internal_intents: set[str] = set()
            executing: set[str] = set()
            for event in connection.execute(
                "SELECT event_type,payload FROM run_events WHERE run_id=? AND event_type IN ('model.message','tool.intended','tool.executing') ORDER BY sequence",
                (run_id,),
            ):
                payload = json.loads(event["payload"])
                if event["event_type"] == "model.message":
                    for call in payload.get("message", {}).get("tool_calls", []):
                        proposals[call["id"]] = call["function"]["name"]
                elif event["event_type"] == "tool.executing":
                    executing.add(payload["call_id"])
                elif payload.get("internal") is True:
                    internal_intents.add(payload["call_id"])
            for row in rows:
                call_id = row["provider_call_id"]
                if (
                    row["tool_name"] not in names
                    or row["tool_version"] != "1"
                    or call_id in executing
                ):
                    continue
                internal = (
                    call_id in internal_intents or proposals.get(call_id) != names[row["tool_name"]]
                )
                result = {
                    "ok": False,
                    "effect": "read_only",
                    "error": {
                        "code": "interrupted_read",
                        "message": "This read-only operation has no saved result. Request a new read if still needed; no previous contents are assumed.",
                    },
                }
                connection.execute(
                    "UPDATE tool_calls SET state='failed',result=?,error=?,updated_at=? WHERE run_id=? AND provider_call_id=? AND state='intended'",
                    (json.dumps(result), json.dumps(result["error"]), utc_now(), run_id, call_id),
                )
                self.record_event(
                    connection,
                    run_id,
                    "tool.failed",
                    {
                        "call_id": call_id,
                        "internal": internal,
                        "effect": "read_only",
                        "code": "interrupted_read",
                    },
                )
                resolved.append(call_id)
        return resolved

    def commit_run_review(
        self, run_id: str, token: str, *, revision: int, accepted: bool, review: dict[str, Any]
    ) -> dict[str, Any]:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.require_lease(connection, run_id, token)
            run = connection.execute(
                "SELECT state,revision FROM runs WHERE id=?", (run_id,)
            ).fetchone()
            if run["state"] != "verifying" or run["revision"] != revision:
                raise ValueError("Run changed; reload the candidate before reviewing.")
            if accepted:
                if connection.execute(
                    "SELECT 1 FROM tasks WHERE run_id=? AND status!='completed' LIMIT 1", (run_id,)
                ).fetchone():
                    raise ValueError(
                        "Every specialist task must be completed before accepting this run."
                    )
                if connection.execute(
                    "SELECT 1 FROM tool_calls WHERE run_id=? AND state='intended' LIMIT 1",
                    (run_id,),
                ).fetchone():
                    raise ValueError(
                        "Uncertain tool actions require reconciliation before acceptance."
                    )
                from xaa.runtime.project_validation import validation_required

                if validation_required(self, run_id):
                    selected = review.get("validation_call_ids", [])
                    validation_hash = review.get("validation_snapshot_sha256")
                    if (
                        not selected
                        or not validation_hash
                        or any(not isinstance(item, str) for item in selected)
                    ):
                        raise ValueError("Current integrated-project validation is required.")
                    validated_ids = {
                        row["provider_call_id"]: json.loads(row["result"])
                        for row in connection.execute(
                            "SELECT tc.provider_call_id,tc.result FROM tool_calls tc JOIN run_events e ON e.run_id=tc.run_id AND e.event_type='execution.reserved' AND json_extract(e.payload,'$.call_id')=tc.provider_call_id WHERE tc.run_id=? AND tc.task_id IS NULL AND tc.state='completed' AND tc.tool_name='workspace.run_command' AND tc.tool_version='1' AND json_extract(e.payload,'$.purpose')='project_validation'",
                            (run_id,),
                        )
                    }
                    if any(
                        item not in validated_ids
                        or not validated_ids[item].get("succeeded")
                        or validated_ids[item].get("snapshot_sha256") != validation_hash
                        for item in selected
                    ):
                        raise ValueError("Selected project validation is unavailable or stale.")
                    from xaa.runtime.task_commands import verify_command_output

                    for item in selected:
                        verify_command_output(self, run_id, validated_ids[item])
            else:
                self._stop_specialists(connection, run_id, "parent_rejected", cancel=True)
            state = "completed" if accepted else "failed"
            self.record_event(
                connection,
                run_id,
                "run.reviewed",
                {"actor": "local-user", **review, "state": state},
            )
            connection.execute(
                "UPDATE runs SET state=?,revision=revision+1,updated_at=? WHERE id=?",
                (state, utc_now(), run_id),
            )
            self.record_event(
                connection,
                run_id,
                "run.state_changed",
                {
                    "from": "verifying",
                    "to": state,
                    "reason": "user_review_accepted" if accepted else "user_review_rejected",
                },
            )
        result = self.get_run(run_id)
        assert result is not None
        return result

    def unresolved_tool_calls(self, run_id: str) -> list[dict[str, Any]]:
        """Return intents requiring destination reconciliation after an interrupted execution."""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM tool_calls WHERE run_id = ? AND state = 'intended' ORDER BY created_at",
                (run_id,),
            ).fetchall()
        return [
            {**dict(row), "id": row["provider_call_id"], "arguments": json.loads(row["arguments"])}
            for row in rows
        ]

    def conversation_messages(self, run_id: str) -> list[dict[str, Any]]:
        """Replay persisted model messages and confirmed results in event order."""
        messages: list[dict[str, Any]] = []
        with self._connection() as connection:
            for event in self.list_events(run_id):
                if event["event_type"] == "model.message":
                    messages.append(public_assistant_message(event["payload"]["message"]))
                elif event["event_type"] in {
                    "verification.feedback",
                    "execution.resume_feedback",
                    "model.answer_requested",
                    "model.response_requested",
                }:
                    messages.append(event["payload"]["message"])
                elif event["event_type"] in {"tool.completed", "tool.failed"}:
                    call_id = event["payload"]["call_id"]
                    internal = event["payload"].get("internal")
                    if internal is True or (
                        internal is None
                        and (call_id.startswith("verification-") or call_id.endswith(":source"))
                    ):
                        continue
                    row = connection.execute(
                        "SELECT result FROM tool_calls WHERE run_id = ? AND provider_call_id = ? AND state IN ('completed','failed')",
                        (run_id, call_id),
                    ).fetchone()
                    if row is None:
                        raise ValueError("Confirmed tool event is missing its result record.")
                    messages.append(
                        {"role": "tool", "tool_call_id": call_id, "content": row["result"]}
                    )
        return messages

    def token_usage(self, run_id: str) -> dict[str, int]:
        return self._token_usage(self.list_events(run_id))

    @staticmethod
    def _token_usage(events: list[dict[str, Any]]) -> dict[str, int]:
        totals = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "unmeasured_calls": 0,
        }
        parent_attempts = {
            event["payload"]["inference_id"]
            for event in events
            if event["event_type"] == "model.inference.started"
        }
        parent_answers = {
            event["payload"].get("inference_id")
            for event in events
            if event["event_type"] in {"model.message", "model.incomplete"}
        }
        diagnostic_attempts = {
            event["payload"]["inference_id"]
            for event in events
            if event["event_type"] == "diagnostic.inference.started"
        }
        diagnostic_answers = {
            event["payload"]["inference_id"]
            for event in events
            if event["event_type"] == "diagnostic.inference.completed"
        }
        child_attempts = sum(event["event_type"] == "task.inference.started" for event in events)
        child_answers = sum(event["event_type"] == "task.model.message" for event in events)
        totals["unmeasured_calls"] = (
            len(diagnostic_attempts - diagnostic_answers)
            + len(parent_attempts - parent_answers)
            + max(0, child_attempts - child_answers)
        )
        for event in events:
            if event["event_type"] not in {
                "model.message",
                "model.incomplete",
                "task.model.message",
                "diagnostic.inference.completed",
            }:
                continue
            usage = event["payload"].get("usage")
            if usage is None:
                totals["unmeasured_calls"] += 1
            else:
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    totals[key] += usage[key]
        return totals

    def usage_report(self, run_id: str) -> dict[str, int]:
        # One event snapshot keeps measured usage and holds consistent across release.
        events = self.list_events(run_id)
        measured = self._token_usage(events)
        answered = {
            event["payload"].get("inference_id")
            for event in events
            if event["event_type"]
            in {
                "model.message",
                "model.incomplete",
                "task.model.message",
                "diagnostic.inference.completed",
            }
            and event["payload"].get("usage") is not None
        }
        pending = [
            event["payload"]
            for event in events
            if event["event_type"] == "tokens.reserved"
            and event["payload"]["inference_id"] not in answered
        ]
        return {
            **measured,
            "outstanding_reserved_tokens": sum(hold["reserved_tokens"] for hold in pending),
            "outstanding_reservations": len(pending),
        }

    def reserve_tokens(
        self,
        run_id: str,
        token: str,
        inference_id: str,
        prompt_tokens: int,
        output_tokens: int,
        *,
        task_id: str | None = None,
        task_token: str | None = None,
    ) -> None:
        """Persist a count-plus-output hold; measured durable answers release it implicitly."""
        if (
            type(prompt_tokens) is not int
            or prompt_tokens < 0
            or type(output_tokens) is not int
            or output_tokens < 1
        ):
            raise ValueError("Invalid token reservation.")
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.require_lease(connection, run_id, token)
            events = self.list_events(run_id)
            starts = {
                e["payload"].get("inference_id")
                for e in events
                if e["event_type"]
                in {
                    "model.inference.started",
                    "task.inference.started",
                    "diagnostic.inference.started",
                }
            }
            if inference_id not in starts:
                raise ValueError("Reservation requires a durable inference attempt.")
            holds = [e["payload"] for e in events if e["event_type"] == "tokens.reserved"]
            if any(h["inference_id"] == inference_id for h in holds):
                raise ValueError("Inference already has a token reservation.")
            answered = {
                e["payload"].get("inference_id")
                for e in events
                if e["event_type"]
                in {
                    "model.message",
                    "model.incomplete",
                    "task.model.message",
                    "diagnostic.inference.completed",
                }
                and e["payload"].get("usage") is not None
            }
            if inference_id in answered:
                raise ValueError("Inference already answered.")
            pending = [h for h in holds if h["inference_id"] not in answered]
            usage = self.token_usage(run_id)
            run = self.get_run(run_id)
            assert run is not None
            limit = run["limits"]["max_total_tokens"]
            amount = prompt_tokens + output_tokens
            if limit is not None and (
                usage["unmeasured_calls"] > 1 + len(pending)
                or usage["total_tokens"] + sum(h["reserved_tokens"] for h in pending) + amount
                > limit
            ):
                raise TokenBudgetExceeded(
                    "Counted prompt and output allowance exceed remaining token budget."
                )
            if task_id is not None:
                row = connection.execute(
                    "SELECT tasks.budgets FROM tasks JOIN task_leases ON tasks.id=task_leases.task_id "
                    "WHERE tasks.id=? AND tasks.status='running' AND task_leases.token=? AND task_leases.expires_at>?",
                    (f"{run_id}:{task_id}", task_token, utc_now()),
                ).fetchone()
                if row is None:
                    raise LeaseLost("Specialist reservation lost ownership.")
                own_limit = json.loads(row[0]).get("max_total_tokens")
                own_total = sum(
                    e["payload"]["usage"]["total_tokens"]
                    for e in events
                    if e["event_type"] == "task.model.message"
                    and e["payload"].get("task_id") == task_id
                    and e["payload"].get("usage") is not None
                )
                own_pending = sum(
                    h["reserved_tokens"] for h in pending if h.get("task_id") == task_id
                )
                if own_limit is not None and own_total + own_pending + amount > own_limit:
                    raise TokenBudgetExceeded(
                        "Specialist counted request exceeds its remaining token allowance."
                    )
            self.record_event(
                connection,
                run_id,
                "tokens.reserved",
                {
                    "inference_id": inference_id,
                    "task_id": task_id,
                    "prompt_tokens": prompt_tokens,
                    "max_output_tokens": output_tokens,
                    "reserved_tokens": amount,
                },
            )

    def check_token_budget(self, run_id: str, token: str, *, before_inference: bool) -> None:
        """Gate further work on durable reported usage, not a hard inference ceiling.

        In-flight/unknown usage fails closed for another inference. After a response,
        equality permits its tools, but no subsequent inference. Never refunds attempts.
        """
        with self.connection() as connection:
            self.require_lease(connection, run_id, token)
        run = self.get_run(run_id)
        assert run is not None
        limit = run["limits"]["max_total_tokens"]
        if limit is None:
            return
        usage = self.token_usage(run_id)
        if (
            usage["unmeasured_calls"]
            or usage["total_tokens"] > limit
            or (before_inference and usage["total_tokens"] == limit)
        ):
            raise TokenBudgetExceeded("Token threshold reached or usage is unknown.")

    def recover_abandoned_runs(self) -> list[str]:
        """Recover only expired ownership periods and return dispatchable queued IDs."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = utc_now()
            abandoned = connection.execute(
                "SELECT id FROM runs WHERE state IN ('planning', 'running') "
                "AND (lease_owner IS NULL OR lease_expires_at <= ?)",
                (now,),
            ).fetchall()
            for row in abandoned:
                run_id = row["id"]
                interrupted = self._stop_specialists(
                    connection, run_id, "parent_worker_lost_reconciliation_required"
                )
                unresolved = connection.execute(
                    "SELECT 1 FROM tool_calls WHERE run_id = ? AND state = 'intended' LIMIT 1",
                    (run_id,),
                ).fetchone()
                destination = "blocked" if unresolved or interrupted else "queued"
                connection.execute(
                    "UPDATE runs SET state = ?, lease_owner = NULL, lease_expires_at = NULL, "
                    "updated_at = ?, revision = revision + 1 WHERE id = ?",
                    (destination, now, run_id),
                )
                self.record_event(
                    connection,
                    run_id,
                    "run.recovered",
                    {
                        "state": destination,
                        "reason": "tool_reconciliation_required"
                        if unresolved or interrupted
                        else "expired_worker",
                    },
                )
            rows = connection.execute(
                "SELECT id FROM runs WHERE state = 'queued' AND model IS NOT NULL "
                "AND (lease_owner IS NULL OR lease_expires_at <= ?)",
                (now,),
            ).fetchall()
            return [row["id"] for row in rows]
