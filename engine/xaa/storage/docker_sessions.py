"""Durable ownership and transitions for opt-in reusable Docker sessions."""

from __future__ import annotations

import json
import re
from typing import Any, Literal, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from xaa.runtime.command_contracts import CommandIntent
from xaa.runtime.docker_execution_policy import (
    DOCKER_CONTAINER_MEMORY_BYTES,
    DOCKER_CONTAINER_NANO_CPUS,
    MAX_REUSABLE_DOCKER_SESSIONS,
)
from xaa.storage.runs import RunStore, utc_now

DockerSessionState = Literal[
    "reserved", "creating", "ready", "busy", "stopping", "closed", "unknown"
]
_TRANSITIONS: dict[DockerSessionState, set[DockerSessionState]] = {
    "reserved": {"creating", "stopping", "unknown"},
    "creating": {"ready", "stopping", "unknown"},
    "ready": {"busy", "stopping", "unknown"},
    "busy": {"ready", "stopping", "unknown"},
    "stopping": {"closed", "unknown"},
    "closed": set(),
    "unknown": {"stopping"},
}


class DockerSession(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    run_id: str
    task_scope: str
    profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    image_reference: str = Field(min_length=1, max_length=320)
    state: DockerSessionState
    container_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    active_execution_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    created_at: str
    updated_at: str


class DockerSessionJournal:
    """Persist session ownership before Docker side effects and fence transitions."""

    def __init__(self, store: RunStore) -> None:
        self.store = store

    def reserve(
        self,
        run_id: str,
        owner: str,
        *,
        task_id: str | None,
        profile_sha256: str,
        image_reference: str,
    ) -> DockerSession:
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self.reserve_in_transaction(
                connection,
                run_id,
                owner,
                task_id=task_id,
                profile_sha256=profile_sha256,
                image_reference=image_reference,
            )

    def reserve_in_transaction(
        self,
        connection: Any,
        run_id: str,
        owner: str,
        *,
        task_id: str | None,
        profile_sha256: str,
        image_reference: str,
    ) -> DockerSession:
        if re.fullmatch(r"[a-f0-9]{64}", profile_sha256) is None:
            raise ValueError("Docker session profile digest is invalid.")
        if (
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}@sha256:[a-f0-9]{64}", image_reference)
            is None
        ):
            raise ValueError("Docker session image reference is invalid.")
        task_scope = task_id or "parent"
        self.store.require_lease(connection, run_id, owner)
        if task_id is not None:
            task = connection.execute(
                "SELECT 1 FROM tasks WHERE id=? AND run_id=?", (task_id, run_id)
            ).fetchone()
            if task is None:
                raise PermissionError("Docker session task is outside the owning run.")
        row = connection.execute(
            "SELECT * FROM docker_sessions WHERE run_id=? AND task_scope=?",
            (run_id, task_scope),
        ).fetchone()
        if row is not None:
            existing = DockerSession.model_validate(dict(row))
            if (
                existing.profile_sha256 != profile_sha256
                or existing.image_reference != image_reference
            ):
                raise PermissionError("Docker session profile changed during its lifetime.")
            if existing.state not in {"reserved", "creating", "ready"}:
                raise RuntimeError("Docker session needs recovery before it can be reused.")
            if existing.state != "ready":
                raise RuntimeError("Docker session is not ready for another command.")
            return existing
        active_sessions = int(
            connection.execute(
                "SELECT COUNT(*) FROM docker_sessions WHERE state!='closed'"
            ).fetchone()[0]
        )
        if active_sessions >= MAX_REUSABLE_DOCKER_SESSIONS:
            memory_mib = (
                DOCKER_CONTAINER_MEMORY_BYTES * MAX_REUSABLE_DOCKER_SESSIONS // (1024 * 1024)
            )
            cpu_cores = DOCKER_CONTAINER_NANO_CPUS * MAX_REUSABLE_DOCKER_SESSIONS / 1_000_000_000
            raise RuntimeError(
                "Docker reusable-session resource budget is full for this profile "
                f"({MAX_REUSABLE_DOCKER_SESSIONS} sessions, up to {memory_mib} MiB and "
                f"{cpu_cores:g} CPU cores). Wait for confirmed cleanup or disable session reuse."
            )
        now = utc_now()
        value = {
            "id": uuid4().hex,
            "run_id": run_id,
            "task_scope": task_scope,
            "profile_sha256": profile_sha256,
            "image_reference": image_reference,
            "state": "reserved",
            "container_id": None,
            "active_execution_id": None,
            "created_at": now,
            "updated_at": now,
        }
        connection.execute(
            "INSERT INTO docker_sessions "
            "(id,run_id,task_scope,profile_sha256,image_reference,state,container_id,active_execution_id,created_at,updated_at) "
            "VALUES (:id,:run_id,:task_scope,:profile_sha256,:image_reference,:state,:container_id,:active_execution_id,:created_at,:updated_at)",
            value,
        )
        return DockerSession.model_validate(value)

    def transition(
        self,
        session_id: str,
        owner: str,
        *,
        expected: set[DockerSessionState],
        destination: DockerSessionState,
        container_id: str | None = None,
        execution_id: str | None = None,
    ) -> DockerSession:
        if re.fullmatch(r"[a-f0-9]{32}", session_id) is None:
            raise ValueError("Docker session identity is invalid.")
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM docker_sessions WHERE id=?", (session_id,)
            ).fetchone()
            if row is None:
                raise KeyError(session_id)
            current = DockerSession.model_validate(dict(row))
            self.store.require_lease(connection, current.run_id, owner)
            if current.state not in expected or destination not in _TRANSITIONS[current.state]:
                raise RuntimeError("Docker session lifecycle transition is stale or forbidden.")
            if destination == "busy":
                if execution_id is None or re.fullmatch(r"[a-f0-9]{32}", execution_id) is None:
                    raise ValueError(
                        "An active Docker session command needs its saved execution ID."
                    )
                if current.container_id is None:
                    raise RuntimeError("A Docker session cannot run before its container is saved.")
            elif execution_id is not None:
                if current.state != "busy" or execution_id != current.active_execution_id:
                    raise ValueError(
                        "Command identity does not match the active Docker session call."
                    )
            if destination == "ready" and current.state == "busy":
                if current.active_execution_id is None:
                    raise RuntimeError("Docker session has no active command to finish.")
                if execution_id != current.active_execution_id:
                    raise RuntimeError("Docker session result does not match its active command.")
            next_container = container_id if container_id is not None else current.container_id
            if current.container_id is not None and container_id not in (
                None,
                current.container_id,
            ):
                raise PermissionError("Docker session container identity cannot be replaced.")
            if destination == "creating" and current.state != "reserved":
                raise RuntimeError("Only a reserved Docker session can begin creation.")
            if (
                destination == "ready"
                and current.state in {"creating", "busy"}
                and next_container is None
            ):
                raise RuntimeError("A ready Docker session must have a verified container ID.")
            if destination == "unknown":
                execution_id = current.active_execution_id
            active_execution = (
                execution_id
                if destination == "busy"
                else current.active_execution_id
                if current.state == "busy" and destination in {"stopping", "unknown"}
                else None
            )
            now = utc_now()
            connection.execute(
                "UPDATE docker_sessions SET state=?,container_id=?,active_execution_id=?,updated_at=? "
                "WHERE id=? AND state=?",
                (destination, next_container, active_execution, now, session_id, current.state),
            )
            saved = connection.execute(
                "SELECT * FROM docker_sessions WHERE id=?", (session_id,)
            ).fetchone()
            return DockerSession.model_validate(cast(Any, dict(saved)))

    def get(self, session_id: str) -> DockerSession | None:
        with self.store.connection() as connection:
            row = connection.execute(
                "SELECT * FROM docker_sessions WHERE id=?", (session_id,)
            ).fetchone()
        return DockerSession.model_validate(dict(row)) if row is not None else None

    def saved_intent(self, session_id: str) -> CommandIntent:
        with self.store.connection() as connection:
            row = connection.execute(
                "SELECT arguments FROM tool_calls WHERE tool_name='workspace.run_command' "
                "AND json_extract(arguments,'$.docker_session_id')=? "
                "ORDER BY updated_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        if row is None:
            raise KeyError(session_id)
        return CommandIntent.model_validate(json.loads(row["arguments"]))

    def finish_cleanup(
        self,
        session_id: str,
        *,
        cleanup_confirmed: bool,
        owner: str | None = None,
        expected_execution_id: str | None = None,
    ) -> DockerSession:
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT s.*,r.state AS run_state,r.lease_owner FROM docker_sessions AS s "
                "JOIN runs AS r ON r.id=s.run_id WHERE s.id=?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError(session_id)
            run_state = str(row["run_state"])
            active_states = {"queued", "planning", "running", "verifying"}
            if run_state in active_states:
                if owner is None or row["lease_owner"] != owner:
                    raise PermissionError("Docker session cleanup requires the live run owner.")
                self.store.require_lease(connection, str(row["run_id"]), owner)
            elif row["lease_owner"] is not None:
                raise PermissionError("Stopped Docker session cleanup requires an unleased run.")
            current = DockerSession.model_validate(
                {key: row[key] for key in DockerSession.model_fields}
            )
            if (
                expected_execution_id is not None
                and current.active_execution_id is not None
                and current.active_execution_id != expected_execution_id
            ):
                raise RuntimeError("Docker session cleanup does not match its active command.")
            if current.state == "closed":
                return current
            destination: DockerSessionState = "closed" if cleanup_confirmed else "unknown"
            active_execution_id = current.active_execution_id if destination == "unknown" else None
            now = utc_now()
            connection.execute(
                "UPDATE docker_sessions SET state=?,active_execution_id=?,updated_at=? "
                "WHERE id=? AND state!=?",
                (destination, active_execution_id, now, session_id, "closed"),
            )
            saved = connection.execute(
                "SELECT * FROM docker_sessions WHERE id=?", (session_id,)
            ).fetchone()
            return DockerSession.model_validate(dict(saved))

    def terminal(self, limit: int = 100) -> list[DockerSession]:
        if not 1 <= limit <= 1000:
            raise ValueError("Docker session cleanup query limit is invalid.")
        with self.store.connection() as connection:
            rows = connection.execute(
                "SELECT s.* FROM docker_sessions AS s JOIN runs AS r ON r.id=s.run_id "
                "WHERE r.state IN ('paused','blocked','waiting_for_approval','completed','cancelled','failed') "
                "AND s.state IN ('stopping','unknown') ORDER BY s.updated_at LIMIT ?",
                (limit,),
            ).fetchall()
        return [DockerSession.model_validate(dict(row)) for row in rows]
