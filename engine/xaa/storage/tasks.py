"""Durable specialist plans sharing the parent run's authority boundary."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import uuid4

from xaa.runtime.task_graph import TaskDefinition, TaskGraph, TaskStatus
from xaa.storage.runs import RunStore, TokenBudgetExceeded, utc_now


class TaskTokenBudgetExceeded(TokenBudgetExceeded):
    """A specialist's own reported token allowance is exhausted or unknown."""


class TaskStore:
    def __init__(self, runs: RunStore) -> None:
        self.runs = runs

    def record_read_worker_event(
        self,
        run_id: str,
        parent_token: str,
        task_id: str,
        task_token: str,
        kind: Literal[
            "task.model.delta",
            "task.model.message",
            "task.tool.result",
            "task.response_requested",
        ],
        payload: dict[str, Any],
    ) -> None:
        """Fence read-only specialist output in the same transaction as its publication."""
        if (
            kind
            not in {
                "task.model.delta",
                "task.model.message",
                "task.tool.result",
                "task.response_requested",
            }
            or payload.get("task_id") != task_id
        ):
            raise ValueError(
                "Specialist output must identify its own task and supported event kind."
            )
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.runs.require_lease(connection, run_id, parent_token)
            self.runs.require_task_read_scope(connection, run_id, task_id, task_token)
            self.runs.record_event(connection, run_id, kind, payload)

    def check_token_budget(self, run_id: str, task_id: str, *, before_inference: bool) -> None:
        task = next(row for row in self.records(run_id) if row["local_id"] == task_id)
        limit = task["budgets"].get("max_total_tokens")
        if limit is None:
            return
        events = [
            event
            for event in self.runs.list_events(run_id)
            if event["payload"].get("task_id") == task_id
        ]
        attempts = sum(event["event_type"] == "task.inference.started" for event in events)
        answers = [event for event in events if event["event_type"] == "task.model.message"]
        unknown = attempts > len(answers) or any(
            event["payload"].get("usage") is None for event in answers
        )
        total = sum(
            event["payload"].get("usage", {}).get("total_tokens", 0)
            for event in answers
            if event["payload"].get("usage") is not None
        )
        if unknown or total > limit or (before_inference and total == limit):
            raise TaskTokenBudgetExceeded("Specialist token allowance exhausted or unmeasured.")

    def reserve_inference(
        self, run_id: str, parent_token: str, task_id: str, task_token: str
    ) -> str:
        """Charge an attempt before inference, including interrupted requests."""
        run = self.runs.get_run(run_id)
        assert run is not None
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.runs.require_lease(connection, run_id, parent_token)
            row = connection.execute(
                "SELECT tasks.budgets FROM tasks JOIN task_leases ON tasks.id=task_leases.task_id WHERE tasks.id=? AND tasks.run_id=? AND tasks.status='running' AND task_leases.token=? AND task_leases.expires_at>?",
                (f"{run_id}:{task_id}", run_id, task_token, utc_now()),
            ).fetchone()
            if row is None:
                raise PermissionError("Task worker no longer holds a live lease.")
            total = self.runs.count_inference_attempts(connection, run_id)
            own = connection.execute(
                "SELECT COUNT(*) FROM run_events WHERE run_id=? AND event_type='task.inference.started' AND json_extract(payload,'$.task_id')=?",
                (run_id, task_id),
            ).fetchone()[0]
            if (
                total >= run["limits"]["max_iterations"]
                or own >= json.loads(row[0])["max_iterations"]
            ):
                raise ValueError("Shared or specialist iteration budget exhausted.")
            inference_id = str(uuid4())
            self.runs.record_event(
                connection,
                run_id,
                "task.inference.started",
                {"task_id": task_id, "inference_id": inference_id},
            )
            return inference_id

    def reserve_tokens(
        self,
        run_id: str,
        parent_token: str,
        inference_id: str,
        prompt_tokens: int,
        output_tokens: int,
        *,
        task_id: str,
        task_token: str,
    ) -> None:
        try:
            self.runs.reserve_tokens(
                run_id,
                parent_token,
                inference_id,
                prompt_tokens,
                output_tokens,
                task_id=task_id,
                task_token=task_token,
            )
        except TokenBudgetExceeded as exc:
            raise TaskTokenBudgetExceeded(str(exc)) from None

    def renew_worker(self, run_id: str, parent_token: str, task_id: str, task_token: str) -> None:
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.runs.require_lease(connection, run_id, parent_token)
            changed = connection.execute(
                "UPDATE task_leases SET expires_at=? WHERE task_id=? AND token=? AND expires_at>? AND EXISTS (SELECT 1 FROM tasks WHERE id=task_id AND run_id=? AND status='running')",
                (
                    (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
                    f"{run_id}:{task_id}",
                    task_token,
                    utc_now(),
                    run_id,
                ),
            ).rowcount
            if changed != 1:
                raise PermissionError("Task worker no longer holds a live lease.")

    def recover_expired(self, run_id: str, parent_token: str) -> list[str]:
        """Fence lost workers without assuming their external actions failed."""
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.runs.require_lease(connection, run_id, parent_token)
            rows = connection.execute(
                "SELECT tasks.id FROM tasks LEFT JOIN task_leases ON tasks.id=task_leases.task_id WHERE tasks.run_id=? AND tasks.status='running' AND (task_leases.expires_at IS NULL OR task_leases.expires_at<=?)",
                (run_id, utc_now()),
            ).fetchall()
            recovered: list[str] = []
            for row in rows:
                identity = row[0]
                local_id = identity[len(run_id) + 1 :]
                connection.execute(
                    "UPDATE tasks SET status='blocked', updated_at=? WHERE id=?",
                    (utc_now(), identity),
                )
                connection.execute("DELETE FROM task_leases WHERE task_id=?", (identity,))
                self.runs.record_event(
                    connection,
                    run_id,
                    "task.blocked",
                    {
                        "task_id": local_id,
                        "reason": "worker_lost_reconciliation_required",
                    },
                )
                recovered.append(local_id)
            return recovered

    def propagate_dependency_blocks(self, run_id: str, parent_token: str) -> None:
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.runs.require_lease(connection, run_id, parent_token)
            _, blocked = self.readiness(run_id)
            for local_id in blocked:
                changed = connection.execute(
                    "UPDATE tasks SET status='blocked', updated_at=? WHERE id=? AND status='queued'",
                    (utc_now(), f"{run_id}:{local_id}"),
                ).rowcount
                if changed:
                    self.runs.record_event(
                        connection,
                        run_id,
                        "task.blocked",
                        {"task_id": local_id, "reason": "dependency_failed"},
                    )

    def claim_ready(self, run_id: str, parent_token: str) -> dict[str, Any] | None:
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.runs.require_lease(connection, run_id, parent_token)
            records = self.records(run_id)
            if not records:
                return None
            ready, blocked = self.readiness(run_id)
            for local_id in blocked:
                connection.execute(
                    "UPDATE tasks SET status='blocked', updated_at=? WHERE id=? AND status='queued'",
                    (utc_now(), f"{run_id}:{local_id}"),
                )
                self.runs.record_event(
                    connection,
                    run_id,
                    "task.blocked",
                    {"task_id": local_id, "reason": "dependency_failed"},
                )
            if not ready:
                return None
            local_id = ready[0]
            record = next(row for row in records if row["local_id"] == local_id)
            identity = record["id"]
            token = str(uuid4())
            expires = (datetime.now(UTC) + timedelta(seconds=30)).isoformat()
            connection.execute(
                "UPDATE tasks SET status='running', updated_at=? WHERE id=? AND status='queued'",
                (utc_now(), identity),
            )
            connection.execute("INSERT INTO task_leases VALUES (?,?,?)", (identity, token, expires))
            self.runs.record_event(connection, run_id, "task.claimed", {"task_id": local_id})
            return {"task": {**record, "status": "running"}, "token": token, "expires_at": expires}

    def record_outcome(
        self,
        run_id: str,
        parent_token: str,
        task_id: str,
        task_token: str,
        *,
        state: str,
        result: dict[str, Any],
    ) -> None:
        if state not in {"verifying", "failed", "blocked"}:
            raise ValueError(
                "Workers may submit only a candidate result, failure or blocked outcome."
            )
        identity = f"{run_id}:{task_id}"
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.runs.require_lease(connection, run_id, parent_token)
            lease = connection.execute(
                "SELECT 1 FROM task_leases WHERE task_id=? AND token=? AND expires_at>?",
                (identity, task_token, utc_now()),
            ).fetchone()
            if lease is None:
                raise PermissionError("Task worker no longer holds a live lease.")
            changed = connection.execute(
                "UPDATE tasks SET status=?, result=?, updated_at=? WHERE id=? AND run_id=? AND status='running'",
                (state, json.dumps(result), utc_now(), identity, run_id),
            ).rowcount
            if changed != 1:
                raise ValueError("Task is not running.")
            connection.execute("DELETE FROM task_leases WHERE task_id=?", (identity,))
            self.runs.record_event(
                connection, run_id, "task.result", {"task_id": task_id, "state": state}
            )

    def validate_plan(
        self, run_id: str, tasks: list[TaskDefinition], *, remaining_tokens: bool = True
    ) -> TaskGraph:
        run = self.runs.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        permissions = {"workspace.read"}
        if run["mode"] == "workspace":
            permissions.add("workspace.write")
            if run.get("command_grant") is not None:
                permissions.add("workspace.command")
        return TaskGraph(
            tasks,
            parent_permissions=permissions,
            iteration_budget=run["limits"]["max_iterations"],
            seconds_budget=run["limits"]["max_seconds"],
            token_budget=(
                max(
                    0,
                    run["limits"]["max_total_tokens"]
                    - (self.runs.token_usage(run_id)["total_tokens"] if remaining_tokens else 0),
                )
                if run["limits"]["max_total_tokens"] is not None
                else None
            ),
            output_limit=run["limits"]["max_output_tokens"],
        )

    def save_plan(
        self, run_id: str, token: str, tasks: list[TaskDefinition], *, parent_headroom: int = 0
    ) -> None:
        graph = self.validate_plan(run_id, tasks)
        run = self.runs.get_run(run_id)
        assert run is not None
        now = utc_now()
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.runs.require_lease(connection, run_id, token)
            if (
                parent_headroom < 0
                or sum(task.max_iterations for task in tasks)
                + parent_headroom
                + self.runs.count_inference_attempts(connection, run_id)
                > run["limits"]["max_iterations"]
            ):
                raise ValueError("Task reservations exceed the remaining shared iteration budget.")
            if connection.execute(
                "SELECT 1 FROM tasks WHERE run_id=? LIMIT 1", (run_id,)
            ).fetchone():
                raise ValueError("A saved plan cannot be silently replaced.")
            specialist_profiles = {
                str(row["role"]): dict(row)
                for row in connection.execute(
                    "SELECT role,display_name,model_route,system_instructions FROM specialist_profiles"
                ).fetchall()
            }
            profile_instructions = {
                str(role): str(row["system_instructions"])
                for role, row in specialist_profiles.items()
            }
            for task in graph.tasks.values():
                connection.execute(
                    "INSERT INTO tasks (id,run_id,parent_task_id,objective,acceptance_criteria,assigned_role,identity_name,model_route,identity_instructions,workspace,permission_scope,budgets,status,checkpoint,result,created_at,updated_at,schema_version) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', NULL, NULL, ?, ?, 1)",
                    (
                        f"{run_id}:{task.id}",
                        run_id,
                        task.objective,
                        json.dumps(task.acceptance_criteria),
                        task.role,
                        specialist_profiles.get(task.role, {}).get("display_name") or task.role,
                        specialist_profiles.get(task.role, {}).get("model_route") or run["model"],
                        profile_instructions.get(task.role, ""),
                        run["workspace"],
                        json.dumps(task.permissions),
                        json.dumps(
                            {
                                "max_iterations": task.max_iterations,
                                "max_seconds": task.max_seconds,
                                **(
                                    {"max_total_tokens": task.max_total_tokens}
                                    if task.max_total_tokens is not None
                                    else {}
                                ),
                                **(
                                    {"max_output_tokens": task.max_output_tokens}
                                    if task.max_output_tokens is not None
                                    else {}
                                ),
                            }
                        ),
                        now,
                        now,
                    ),
                )
            for task in graph.tasks.values():
                for dependency in task.dependencies:
                    connection.execute(
                        "INSERT INTO task_dependencies VALUES (?,?)",
                        (f"{run_id}:{task.id}", f"{run_id}:{dependency}"),
                    )
            self.runs.record_event(
                connection, run_id, "plan.saved", {"task_ids": list(graph.tasks)}
            )

    def records(self, run_id: str) -> list[dict[str, Any]]:
        prefix = run_id + ":"
        with self.runs.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks WHERE run_id=? ORDER BY created_at,id", (run_id,)
            ).fetchall()
            records: list[dict[str, Any]] = []
            for row in rows:
                record = dict(row)
                record["local_id"] = record["id"][len(prefix) :]
                record["dependencies"] = [
                    item[0][len(prefix) :]
                    for item in connection.execute(
                        "SELECT depends_on_task_id FROM task_dependencies WHERE task_id=? ORDER BY depends_on_task_id",
                        (record["id"],),
                    ).fetchall()
                ]
                for field in ("acceptance_criteria", "permission_scope", "budgets"):
                    record[field] = json.loads(record[field])
                records.append(record)
        statuses = {record["local_id"]: record["status"] for record in records}
        for record in records:
            record["blocked_reason"] = None
            if record["status"] != "blocked":
                continue
            outcome: dict[str, Any] = json.loads(record["result"]) if record["result"] else {}
            if "TaskTokenBudgetExceeded" in outcome.get("causes", []):
                record["blocked_reason"] = "token_budget_exhausted_or_unmeasured"
            elif any(
                statuses.get(dependency) in {"failed", "blocked", "cancelled"}
                for dependency in record["dependencies"]
            ):
                record["blocked_reason"] = "dependency_failed"
            else:
                record["blocked_reason"] = "execution_blocked"
        return records

    def readiness(self, run_id: str) -> tuple[list[str], list[str]]:
        records = self.records(run_id)
        tasks = [
            TaskDefinition(
                id=row["local_id"],
                objective=row["objective"],
                acceptance_criteria=row["acceptance_criteria"],
                dependencies=row["dependencies"],
                role=row["assigned_role"],
                permissions=row["permission_scope"],
                **row["budgets"],
            )
            for row in records
        ]
        graph = self.validate_plan(run_id, tasks, remaining_tokens=False)
        return graph.readiness(
            {row["local_id"]: cast(TaskStatus, row["status"]) for row in records}
        )
