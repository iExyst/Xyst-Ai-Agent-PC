"""Durable specialist plans sharing the parent run's authority boundary."""

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import uuid4

from xaa.runtime.task_graph import TaskDefinition, TaskGraph, TaskStatus
from xaa.storage.mcp import MCPRegistry
from xaa.storage.runs import RunStore, TokenBudgetExceeded, utc_now


class TaskTokenBudgetExceeded(TokenBudgetExceeded):
    """A specialist's own reported token allowance is exhausted or unknown."""


class TaskStore:
    def __init__(self, runs: RunStore) -> None:
        self.runs = runs

    @staticmethod
    def _task_elapsed_seconds(events: list[dict[str, Any]]) -> float:
        elapsed = 0.0
        started_at: datetime | None = None
        for event in events:
            if event["payload"].get("task_id") is None:
                continue
            if event["event_type"] == "task.claimed" and started_at is None:
                started_at = datetime.fromisoformat(event["created_at"])
            elif started_at is not None and event["event_type"] in {
                "task.blocked",
                "task.result",
                "task.recovery_decided",
            }:
                elapsed += max(
                    0.0,
                    (datetime.fromisoformat(event["created_at"]) - started_at).total_seconds(),
                )
                started_at = None
        if started_at is not None:
            elapsed += max(0.0, (datetime.now(UTC) - started_at).total_seconds())
        return elapsed

    def record_read_worker_event(
        self,
        run_id: str,
        parent_token: str,
        task_id: str,
        task_token: str,
        kind: Literal[
            "task.model.delta",
            "task.model.message",
            "task.model.incomplete",
            "task.model.stream_rejected",
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
                "task.model.incomplete",
                "task.model.stream_rejected",
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
        answers = [
            event
            for event in events
            if event["event_type"] in {"task.model.message", "task.model.incomplete"}
        ]
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
            recovered_ids = {
                event["payload"].get("task_id")
                for event in self.runs.list_events(run_id)
                if event["event_type"] == "task.recovery_decided"
                and event["payload"].get("decision") == "resume"
            }
            local_id = next((identity for identity in ready if identity in recovered_ids), ready[0])
            record = next(row for row in records if row["local_id"] == local_id)
            if local_id in recovered_ids:
                task_events = [
                    event
                    for event in self.runs.list_events(run_id)
                    if event["payload"].get("task_id") == local_id
                ]
                task_elapsed = self._task_elapsed_seconds(task_events)
                record["budgets"]["max_seconds"] = max(
                    0.0, record["budgets"]["max_seconds"] - task_elapsed
                )
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
        if run.get("approved_web_hosts"):
            permissions.add("web.read")
        if run.get("approved_web_search"):
            permissions.add("web.search")
        if run.get("approved_browser") and run.get("approved_web_hosts"):
            permissions.add("web.browser.read")
        permissions.update(
            tool.qualified_name
            for tool in MCPRegistry(self.runs).list_tools()
            if tool.permission == "read"
        )
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
            recovery_reasons = {
                str(item[0])
                for item in connection.execute(
                    "SELECT json_extract(payload,'$.task_id') FROM run_events WHERE run_id=? AND event_type='task.blocked' AND json_extract(payload,'$.reason') IN (?,?)",
                    (
                        run_id,
                        "worker_lost_reconciliation_required",
                        "parent_worker_lost_reconciliation_required",
                    ),
                ).fetchall()
                if item[0] is not None
            }
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
            if record["local_id"] in recovery_reasons:
                record["blocked_reason"] = "worker_lost_reconciliation_required"
            elif "ContextLimitError" in outcome.get("causes", []):
                record["blocked_reason"] = "prompt_count_mismatch"
            elif "ReviewerEvidenceMissing" in outcome.get("causes", []):
                record["blocked_reason"] = "review_evidence_missing"
            elif "TaskTokenBudgetExceeded" in outcome.get("causes", []):
                record["blocked_reason"] = "token_budget_exhausted_or_unmeasured"
            elif any(
                statuses.get(dependency) in {"failed", "blocked", "cancelled"}
                for dependency in record["dependencies"]
            ):
                record["blocked_reason"] = "dependency_failed"
            else:
                record["blocked_reason"] = "execution_blocked"
        return records

    def recovery_summary(self, run_id: str, task_id: str) -> dict[str, Any]:
        """Summarize durable worker evidence without treating a missing result as success."""
        task = next((row for row in self.records(run_id) if row["local_id"] == task_id), None)
        if task is None:
            raise KeyError(task_id)
        if (
            task["status"] != "blocked"
            or task["blocked_reason"] != "worker_lost_reconciliation_required"
        ):
            raise ValueError("This task does not require lost-worker reconciliation.")
        events = [
            event
            for event in self.runs.list_events(run_id)
            if event["payload"].get("task_id") == task_id
        ]
        messages = [
            {"sequence": event["sequence"], "message": event["payload"]["message"]}
            for event in events
            if event["event_type"] == "task.model.message"
            and isinstance(event["payload"].get("message"), dict)
        ]
        calls: list[dict[str, Any]] = [
            call
            for item in messages
            for call in cast(list[dict[str, Any]], item["message"].get("tool_calls", []))
        ]
        proposed_ids: Counter[str] = Counter()
        completed_ids: Counter[str] = Counter(
            event["payload"]["call_id"]
            for event in events
            if event["event_type"] == "task.tool.result"
            and isinstance(event["payload"].get("call_id"), str)
        )
        pending_calls: list[dict[str, str | None]] = []
        for call in calls:
            call_id = call.get("id")
            if not isinstance(call_id, str):
                pending_calls.append({"call_id": None, "name": None})
                continue
            proposed_ids[call_id] += 1
            if completed_ids[call_id] < proposed_ids[call_id]:
                function = cast(dict[str, Any], call.get("function", {}))
                name = function.get("name")
                pending_calls.append(
                    {"call_id": call_id, "name": name if isinstance(name, str) else None}
                )
        candidate_item = next(
            (
                item
                for item in reversed(messages)
                for message in [item["message"]]
                if isinstance(message.get("content"), str)
                and message["content"].strip()
                and not message.get("tool_calls")
            ),
            None,
        )
        candidate = candidate_item["message"]["content"] if candidate_item else None
        last_message_sequence = messages[-1]["sequence"] if messages else -1
        completed_results = [event for event in events if event["event_type"] == "task.tool.result"]
        unresolved = [
            item
            for item in self.runs.unresolved_tool_calls(run_id)
            if item.get("task_id") == task_id
        ]
        pending_command_ids = {
            item["call_id"]
            for item in pending_calls
            if item["name"] == "workspace_run_command" and item["call_id"] is not None
        }
        reservations: dict[str, str] = {}
        for event in self.runs.list_events(run_id):
            payload = event["payload"]
            model_call_id = payload.get("model_call_id")
            command_call_id = payload.get("call_id")
            if (
                event["event_type"] == "execution.reserved"
                and payload.get("task_id") == task_id
                and model_call_id in pending_command_ids
                and isinstance(command_call_id, str)
            ):
                reservations[model_call_id] = command_call_id
        with self.runs.connection() as connection:
            saved_commands = {
                row["provider_call_id"]: row
                for row in connection.execute(
                    "SELECT provider_call_id,tool_name,state,arguments,result FROM tool_calls "
                    "WHERE run_id=? AND task_id=? AND tool_name IN "
                    "('workspace.run_command','workspace.read_file','workspace.list_directory',"
                    "'workspace.write_file','workspace.create_file','workspace.delete_file',"
                    "'web.fetch','web.search','browser.run','mcp.call') "
                    "AND state IN ('intended','completed','failed')",
                    (run_id, f"{run_id}:{task_id}"),
                )
                if row["provider_call_id"]
            }
        recoverable_commands: list[dict[str, Any]] = []
        for model_call_id in pending_command_ids:
            journal_call_id = reservations.get(model_call_id)
            command = saved_commands.get(journal_call_id)
            if command is None:
                continue
            arguments = cast(dict[str, Any], json.loads(command["arguments"]))
            argv = cast(list[Any] | None, arguments.get("argv"))
            if not isinstance(argv, list) or any(not isinstance(item, str) for item in argv):
                continue
            recoverable_commands.append(
                {
                    "model_call_id": model_call_id,
                    "journal_call_id": journal_call_id,
                    "argv": argv,
                    "state": command["state"],
                }
            )
        branch_tools = {
            "workspace_read_file": "workspace.read_file",
            "workspace_list_directory": "workspace.list_directory",
            "workspace_write_file": "workspace.write_file",
            "workspace_write_lines": "workspace.write_file",
            "workspace_create_file": "workspace.create_file",
            "workspace_delete_file": "workspace.delete_file",
            "web_fetch": "web.fetch",
            "web_search": "web.search",
            "browser_run": "browser.run",
        }
        reviewer_read_tools = {
            "review_dependency_file": "review_dependency_file",
            "review_dependency_output": "review_dependency_output",
        }
        recoverable_task_actions: list[dict[str, str]] = []
        scope_permissions = set(task["permission_scope"])
        required_permissions = {
            "workspace.read_file": "workspace.read",
            "workspace.list_directory": "workspace.read",
            "workspace.write_file": "workspace.write",
            "workspace.create_file": "workspace.write",
            "workspace.delete_file": "workspace.write",
            "web.fetch": "web.read",
            "web.search": "web.search",
            "browser.run": "web.browser.read",
        }
        for call in calls:
            model_call_id = call.get("id")
            function = cast(dict[str, Any], call.get("function", {}))
            model_tool_name = cast(str, function.get("name"))
            tool_name = branch_tools.get(model_tool_name)
            if tool_name is None and model_tool_name in scope_permissions:
                tool_name = "mcp.call"
            if tool_name is not None:
                required_permission = (
                    model_tool_name if tool_name == "mcp.call" else required_permissions[tool_name]
                )
                if required_permission not in scope_permissions:
                    tool_name = None
            reviewer_read = (
                model_tool_name in reviewer_read_tools
                and task["assigned_role"] == "reviewer"
                and "workspace.read" in scope_permissions
            )
            if reviewer_read:
                tool_name = reviewer_read_tools[model_tool_name]
            if (
                not isinstance(model_call_id, str)
                or tool_name is None
                or proposed_ids[model_call_id] != 1
            ):
                continue
            journal_call_id = (
                "taskfs-"
                + hashlib.sha256(f"{run_id}:{task_id}:{model_call_id}".encode()).hexdigest()
            )
            saved = saved_commands.get(journal_call_id)
            if reviewer_read:
                if saved is not None:
                    continue
                recoverable_task_actions.append(
                    {
                        "model_call_id": model_call_id,
                        "journal_call_id": journal_call_id,
                        "tool_name": tool_name,
                        "state": "intended",
                    }
                )
                continue
            if (
                saved is None
                or saved["tool_name"] != tool_name
                or completed_ids[model_call_id] >= proposed_ids[model_call_id]
            ):
                continue
            recoverable_state = saved["state"]
            saved_arguments = cast(dict[str, Any], json.loads(saved["arguments"]))
            schema_digest = saved_arguments.get("schema_sha256")
            mcp_read = (
                tool_name == "mcp.call"
                and saved_arguments.get("tool") == model_tool_name
                and saved_arguments.get("permission") == "read"
                and isinstance(schema_digest, str)
                and len(schema_digest) == 64
                and all(character in "0123456789abcdef" for character in schema_digest)
            )
            can_attach_completed = (
                recoverable_state == "completed"
                and saved["result"] is not None
                and (tool_name != "mcp.call" or mcp_read)
            )
            can_seal_read = (
                tool_name
                in {
                    "workspace.read_file",
                    "workspace.list_directory",
                    "web.fetch",
                    "web.search",
                    "browser.run",
                }
                and recoverable_state == "intended"
            ) or (mcp_read and recoverable_state == "intended")
            can_attach_interrupted = (
                tool_name
                in {
                    "workspace.read_file",
                    "workspace.list_directory",
                    "web.fetch",
                    "web.search",
                    "browser.run",
                }
                and recoverable_state == "failed"
                and saved["result"] is not None
                and json.loads(saved["result"]).get("error", {}).get("code") == "interrupted_read"
            ) or (
                mcp_read
                and recoverable_state == "failed"
                and saved["result"] is not None
                and json.loads(saved["result"]).get("error", {}).get("code") == "interrupted_read"
            )
            if not (can_attach_completed or can_seal_read or can_attach_interrupted):
                continue
            action = {
                "model_call_id": model_call_id,
                "journal_call_id": journal_call_id,
                "tool_name": tool_name,
                "state": recoverable_state,
            }
            if tool_name == "mcp.call":
                action["mcp_tool"] = model_tool_name
            recoverable_task_actions.append(action)
        candidate_reviewable = bool(
            candidate_item
            and candidate_item["sequence"] == last_message_sequence
            and not pending_calls
            and not unresolved
        )
        resume_from_start = (
            not messages
            and not calls
            and not completed_results
            and not pending_calls
            and not unresolved
            and not any(event["event_type"] == "task.response_requested" for event in events)
        )
        safe_boundary = resume_from_start or (
            bool(messages)
            and not pending_calls
            and not unresolved
            and (
                not calls
                or (
                    all(completed_ids[call_id] >= count for call_id, count in proposed_ids.items())
                    and completed_results
                    and completed_results[-1]["sequence"] > last_message_sequence
                )
            )
        )
        answered_inferences = {
            event["payload"].get("inference_id")
            for event in events
            if event["event_type"] in {"task.model.message", "task.model.incomplete"}
            and event["payload"].get("task_id") == task_id
            and event["payload"].get("usage") is not None
        }
        pending_reservations = [
            event["payload"]
            for event in events
            if event["event_type"] == "tokens.reserved"
            and event["payload"].get("task_id") == task_id
            and event["payload"].get("inference_id") not in answered_inferences
        ]
        tokens_reserved = sum(item.get("reserved_tokens", 0) for item in pending_reservations)
        task_attempts = sum(event["event_type"] == "task.inference.started" for event in events)
        task_answers = [
            event
            for event in events
            if event["event_type"] in {"task.model.message", "task.model.incomplete"}
        ]
        run = self.runs.get_run(run_id)
        assert run is not None
        task_answered_ids = {
            event["payload"].get("inference_id")
            for event in task_answers
            if isinstance(event["payload"].get("inference_id"), str)
        }
        task_reserved_ids = {
            event["payload"].get("inference_id")
            for event in events
            if event["event_type"] == "tokens.reserved"
            and isinstance(event["payload"].get("inference_id"), str)
        }
        unadmitted_task_attempts = {
            event["payload"].get("inference_id")
            for event in events
            if event["event_type"] == "task.inference.started"
            and isinstance(event["payload"].get("inference_id"), str)
            and event["payload"].get("inference_id") not in task_answered_ids
            and event["payload"].get("inference_id") not in task_reserved_ids
        }
        task_token_limit = task["budgets"].get("max_total_tokens")
        task_reported_tokens = sum(
            event["payload"]["usage"]["total_tokens"]
            for event in task_answers
            if event["payload"].get("usage") is not None
        )
        task_unmeasured_calls = max(0, task_attempts - len(task_answers)) + sum(
            event["payload"].get("usage") is None for event in task_answers
        )
        exact_reservations_required = run["limits"].get("pre_inference_token_reservations") is True
        task_usage_unknown = task_unmeasured_calls > 0
        if exact_reservations_required and task_unmeasured_calls == len(unadmitted_task_attempts):
            # Exact mode writes a hold before sending an inference request. An unanswered
            # attempt without a hold is a safe preflight boundary, not unknown model usage.
            task_usage_unknown = False
        task_budget_available = (
            task_attempts < task["budgets"]["max_iterations"]
            and (
                task_token_limit is None
                or (not task_usage_unknown and task_reported_tokens < task_token_limit)
            )
            and self._task_elapsed_seconds(events) < task["budgets"]["max_seconds"]
        )
        run_events = self.runs.list_events(run_id)
        run_answered_ids = {
            event["payload"].get("inference_id")
            for event in run_events
            if event["event_type"] in {"task.model.message", "task.model.incomplete"}
        }
        run_reserved_ids = {
            event["payload"].get("inference_id")
            for event in run_events
            if event["event_type"] == "tokens.reserved"
        }
        planning_starts = [
            event["created_at"]
            for event in run_events
            if event["event_type"] == "run.state_changed"
            and event["payload"].get("to") == "planning"
        ]
        elapsed = (
            datetime.now(UTC)
            - datetime.fromisoformat(planning_starts[0] if planning_starts else run["created_at"])
        ).total_seconds()
        run_usage = self.runs.token_usage(run_id)
        shared_limit = run["limits"]["max_total_tokens"]
        run_unadmitted_attempts = {
            event["payload"].get("inference_id")
            for event in run_events
            if event["event_type"] == "task.inference.started"
            and isinstance(event["payload"].get("inference_id"), str)
            and event["payload"].get("inference_id") not in run_answered_ids
            and event["payload"].get("inference_id") not in run_reserved_ids
        }
        run_usage_unknown = run_usage["unmeasured_calls"] > 0
        if exact_reservations_required and run_usage["unmeasured_calls"] == len(
            run_unadmitted_attempts
        ):
            run_usage_unknown = False
        run_budget_available = (
            self.runs.inference_attempts(run_id) < run["limits"]["max_iterations"]
            and elapsed < run["limits"]["max_seconds"]
            and self.runs.usage_report(run_id)["outstanding_reserved_tokens"] == 0
            and (
                shared_limit is None
                or (not run_usage_unknown and run_usage["total_tokens"] < shared_limit)
            )
        )
        safe_to_resume = (
            safe_boundary
            and candidate is None
            and tokens_reserved == 0
            and task_budget_available
            and run_budget_available
        )
        summary: dict[str, Any] = {
            "task_id": task_id,
            "status": task["status"],
            "candidate": candidate,
            "candidate_reviewable": candidate_reviewable and not unresolved,
            "saved_tool_results": sum(
                event["event_type"] == "task.tool.result" for event in events
            ),
            "pending_model_tool_calls": pending_calls,
            "unresolved_tool_calls": len(unresolved),
            "recoverable_commands": recoverable_commands,
            "recoverable_task_actions": recoverable_task_actions,
            "safe_to_resume": safe_to_resume,
            "resume_from_start": resume_from_start and safe_to_resume,
            "consumed_iterations": task_attempts,
            "run_measured_tokens": self.runs.token_usage(run_id)["total_tokens"],
            "reserved_tokens": tokens_reserved,
        }
        return summary

    def record_recovered_command_result(
        self, run_id: str, token: str, task_id: str, model_call_id: str, journal_call_id: str
    ) -> None:
        """Attach a sealed task command result to its pending model tool call exactly once."""
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.runs.require_lease(connection, run_id, token)
            task = connection.execute(
                "SELECT status FROM tasks WHERE run_id=? AND id=?",
                (run_id, f"{run_id}:{task_id}"),
            ).fetchone()
            blocked = connection.execute(
                "SELECT json_extract(payload,'$.reason') FROM run_events "
                "WHERE run_id=? AND event_type='task.blocked' "
                "AND json_extract(payload,'$.task_id')=? ORDER BY sequence DESC LIMIT 1",
                (run_id, task_id),
            ).fetchone()
            if (
                task is None
                or task["status"] != "blocked"
                or blocked is None
                or blocked[0] != "worker_lost_reconciliation_required"
            ):
                raise ValueError("Task recovery state changed; refresh the plan.")
            tool = connection.execute(
                "SELECT tool_name,state,result FROM tool_calls WHERE run_id=? AND task_id=? AND provider_call_id=?",
                (run_id, f"{run_id}:{task_id}", journal_call_id),
            ).fetchone()
            reservation = connection.execute(
                "SELECT payload FROM run_events WHERE run_id=? AND event_type='execution.reserved' AND json_extract(payload,'$.call_id')=?",
                (run_id, journal_call_id),
            ).fetchone()
            if tool is None or tool["tool_name"] != "workspace.run_command":
                raise ValueError("Saved task command is unavailable for reconciliation.")
            if tool["state"] not in {"completed", "failed"} or tool["result"] is None:
                raise ValueError("Saved command still needs backend reconciliation.")
            if reservation is None:
                raise ValueError("Saved task command has no durable reservation.")
            reservation_payload = json.loads(reservation[0])
            if (
                reservation_payload.get("task_id") != task_id
                or reservation_payload.get("model_call_id") != model_call_id
            ):
                raise PermissionError("Saved command belongs to a different task action.")
            model_calls = [
                call
                for row in connection.execute(
                    "SELECT payload FROM run_events WHERE run_id=? AND event_type='task.model.message'",
                    (run_id,),
                )
                for call in json.loads(row[0]).get("message", {}).get("tool_calls", [])
                if json.loads(row[0]).get("task_id") == task_id
                and call.get("id") == model_call_id
                and call.get("function", {}).get("name") == "workspace_run_command"
            ]
            if len(model_calls) != 1:
                raise ValueError("Pending model command is missing or ambiguous.")
            if connection.execute(
                "SELECT 1 FROM run_events WHERE run_id=? AND event_type='task.tool.result' AND json_extract(payload,'$.task_id')=? AND json_extract(payload,'$.call_id')=?",
                (run_id, task_id, model_call_id),
            ).fetchone():
                raise ValueError("Task command result was already attached to its model call.")
            result = json.loads(tool["result"])
            self.runs.record_event(
                connection,
                run_id,
                "task.tool.result",
                {
                    "task_id": task_id,
                    "call_id": model_call_id,
                    "journal_id": journal_call_id,
                    "result": result,
                    "recovered": True,
                },
            )

    def record_recovered_task_action_result(
        self, run_id: str, token: str, task_id: str, model_call_id: str, journal_call_id: str
    ) -> None:
        """Attach a saved task action result or seal an interrupted read; never replay it."""
        allowed = {
            "workspace.read_file",
            "workspace.list_directory",
            "workspace.write_file",
            "workspace.create_file",
            "workspace.delete_file",
            "review_dependency_file",
            "review_dependency_output",
            "web.fetch",
            "web.search",
            "browser.run",
            "mcp.call",
        }
        expected_journal_id = (
            "taskfs-" + hashlib.sha256(f"{run_id}:{task_id}:{model_call_id}".encode()).hexdigest()
        )
        if journal_call_id != expected_journal_id:
            raise PermissionError("Saved task action identity does not match its model call.")
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.runs.require_lease(connection, run_id, token)
            task = connection.execute(
                "SELECT status,permission_scope,assigned_role FROM tasks WHERE run_id=? AND id=?",
                (run_id, f"{run_id}:{task_id}"),
            ).fetchone()
            blocked = connection.execute(
                "SELECT json_extract(payload,'$.reason') FROM run_events "
                "WHERE run_id=? AND event_type='task.blocked' "
                "AND json_extract(payload,'$.task_id')=? ORDER BY sequence DESC LIMIT 1",
                (run_id, task_id),
            ).fetchone()
            if (
                task is None
                or task["status"] != "blocked"
                or blocked is None
                or blocked[0] != "worker_lost_reconciliation_required"
            ):
                raise ValueError("Task recovery state changed; refresh the plan.")
            tool = connection.execute(
                "SELECT tool_name,state,arguments,result FROM tool_calls "
                "WHERE run_id=? AND task_id=? AND provider_call_id=?",
                (run_id, f"{run_id}:{task_id}", journal_call_id),
            ).fetchone()
            model_calls = [
                call
                for row in connection.execute(
                    "SELECT payload FROM run_events WHERE run_id=? AND event_type='task.model.message'",
                    (run_id,),
                )
                for call in json.loads(row[0]).get("message", {}).get("tool_calls", [])
                if json.loads(row[0]).get("task_id") == task_id and call.get("id") == model_call_id
            ]
            if len(model_calls) != 1:
                raise ValueError("Pending model task action is missing or ambiguous.")
            model_tool_name = model_calls[0].get("function", {}).get("name")
            scope_permissions: set[str] = (
                set(cast(list[str], json.loads(task["permission_scope"]))) if task else set()
            )
            expected_tool = {
                "workspace_read_file": "workspace.read_file",
                "workspace_list_directory": "workspace.list_directory",
                "workspace_write_file": "workspace.write_file",
                "workspace_write_lines": "workspace.write_file",
                "workspace_create_file": "workspace.create_file",
                "workspace_delete_file": "workspace.delete_file",
                "review_dependency_file": "review_dependency_file",
                "review_dependency_output": "review_dependency_output",
                "web_fetch": "web.fetch",
                "web_search": "web.search",
                "browser_run": "browser.run",
            }.get(model_tool_name)
            if (
                expected_tool is None
                and isinstance(model_tool_name, str)
                and model_tool_name in scope_permissions
            ):
                expected_tool = "mcp.call"
            if expected_tool not in allowed:
                raise PermissionError("Saved tool is outside task recovery scope.")
            required_permission = (
                model_tool_name
                if expected_tool == "mcp.call"
                else {
                    "workspace.read_file": "workspace.read",
                    "workspace.list_directory": "workspace.read",
                    "workspace.write_file": "workspace.write",
                    "workspace.create_file": "workspace.write",
                    "workspace.delete_file": "workspace.write",
                    "review_dependency_file": "workspace.read",
                    "review_dependency_output": "workspace.read",
                    "web.fetch": "web.read",
                    "web.search": "web.search",
                    "browser.run": "web.browser.read",
                }[expected_tool]
            )
            if required_permission not in scope_permissions:
                raise PermissionError("Task no longer has permission for this saved read.")
            mcp_read = expected_tool == "mcp.call"
            if mcp_read:
                if tool is None:
                    raise ValueError("No saved MCP read action is available to attach.")
                arguments = cast(dict[str, Any], json.loads(tool["arguments"]))
                schema_digest = arguments.get("schema_sha256")
                if (
                    tool["tool_name"] != "mcp.call"
                    or arguments.get("tool") != model_tool_name
                    or arguments.get("permission") != "read"
                    or not isinstance(schema_digest, str)
                    or len(schema_digest) != 64
                    or any(character not in "0123456789abcdef" for character in schema_digest)
                ):
                    raise PermissionError("Saved MCP action is not an inherited read-only call.")
            dependency_read = expected_tool in {
                "review_dependency_file",
                "review_dependency_output",
            }
            if dependency_read:
                if (
                    task["assigned_role"] != "reviewer"
                    or "workspace.read" not in json.loads(task["permission_scope"])
                    or tool is not None
                ):
                    raise PermissionError(
                        "Dependency reads require a reviewer task and no saved effect row."
                    )
                result = {
                    "ok": False,
                    "effect": "read_only",
                    "error": {
                        "code": "interrupted_read",
                        "message": "This dependency read has no saved result. No dependency contents are assumed; request the read again if the dependency remains available.",
                    },
                }
            elif tool is None or tool["tool_name"] not in allowed:
                raise ValueError("No saved task action is available to attach.")
            elif tool["tool_name"] != expected_tool:
                raise PermissionError("Saved action result belongs to a different model call.")
            elif tool["state"] == "intended":
                if (
                    tool["tool_name"]
                    not in {
                        "workspace.read_file",
                        "workspace.list_directory",
                        "web.fetch",
                        "web.search",
                        "browser.run",
                    }
                    and not mcp_read
                ):
                    raise PermissionError("An interrupted write cannot be resolved as a read.")
                if connection.execute(
                    "SELECT 1 FROM run_events WHERE run_id=? AND event_type='tool.executing' "
                    "AND json_extract(payload,'$.call_id')=?",
                    (run_id, journal_call_id),
                ).fetchone():
                    raise ValueError("Read has execution evidence; inspect its saved result first.")
                result = {
                    "ok": False,
                    "effect": "read_only",
                    "error": {
                        "code": "interrupted_read",
                        "message": "This read-only operation has no saved result. Request a new read if still needed; no previous contents are assumed.",
                    },
                }
                connection.execute(
                    "UPDATE tool_calls SET state='failed',result=?,error=?,updated_at=? "
                    "WHERE run_id=? AND provider_call_id=? AND state='intended'",
                    (
                        json.dumps(result),
                        json.dumps(result["error"]),
                        utc_now(),
                        run_id,
                        journal_call_id,
                    ),
                )
                self.runs.record_event(
                    connection,
                    run_id,
                    "tool.failed",
                    {
                        "call_id": journal_call_id,
                        "internal": True,
                        "effect": "read_only",
                        "code": "interrupted_read",
                    },
                )
            else:
                if tool["result"] is None:
                    raise ValueError("Saved task action has no durable result to attach.")
                result = json.loads(tool["result"])
                if (
                    tool["state"] == "failed"
                    and result.get("error", {}).get("code") != "interrupted_read"
                ):
                    raise ValueError(
                        "Only a sealed interrupted read can be attached after failure."
                    )
            if not dependency_read and tool is not None and expected_tool != tool["tool_name"]:
                raise PermissionError("Saved action result belongs to a different model call.")
            if not dependency_read:
                assert tool is not None
                required_permission = (
                    model_tool_name
                    if mcp_read
                    else "workspace.write"
                    if tool["tool_name"]
                    in {"workspace.write_file", "workspace.create_file", "workspace.delete_file"}
                    else "workspace.read"
                )
                if required_permission not in json.loads(task["permission_scope"]):
                    raise PermissionError("Task did not have permission for the saved action.")
            if connection.execute(
                "SELECT 1 FROM run_events WHERE run_id=? AND event_type='task.tool.result' "
                "AND json_extract(payload,'$.task_id')=? AND json_extract(payload,'$.call_id')=?",
                (run_id, task_id, model_call_id),
            ).fetchone():
                raise ValueError("File action result was already attached to its model call.")
            self.runs.record_event(
                connection,
                run_id,
                "task.tool.result",
                {
                    "task_id": task_id,
                    "call_id": model_call_id,
                    "journal_id": journal_call_id,
                    "result": result,
                    "recovered": True,
                },
            )

    def resolve_lost_worker(self, run_id: str, token: str, task_id: str, decision: str) -> None:
        if decision not in {"resume", "review_candidate", "fail"}:
            raise ValueError("Choose resume, review_candidate, or fail.")
        summary = self.recovery_summary(run_id, task_id)
        if decision == "resume" and not summary["safe_to_resume"]:
            raise ValueError(
                "Review the saved candidate or reconcile pending work before resuming."
            )
        if decision == "review_candidate" and not summary["candidate_reviewable"]:
            raise ValueError("No final candidate is available without later or unresolved work.")
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.runs.require_lease(connection, run_id, token)
            current = next(row for row in self.records(run_id) if row["local_id"] == task_id)
            if (
                current["status"] != "blocked"
                or current["blocked_reason"] != "worker_lost_reconciliation_required"
            ):
                raise ValueError("Task recovery state changed; refresh the plan.")
            destination = {
                "resume": "queued",
                "review_candidate": "verifying",
                "fail": "failed",
            }[decision]
            changed = connection.execute(
                "UPDATE tasks SET status=?,result=CASE WHEN ?='review_candidate' THEN ? ELSE result END,updated_at=? WHERE id=? AND run_id=? AND status='blocked'",
                (
                    destination,
                    decision,
                    json.dumps({"candidate": summary["candidate"]}),
                    utc_now(),
                    f"{run_id}:{task_id}",
                    run_id,
                ),
            ).rowcount
            if changed != 1:
                raise ValueError("Task recovery state changed; refresh the plan.")
            self.runs.record_event(
                connection,
                run_id,
                "task.recovery_decided",
                {
                    "task_id": task_id,
                    "decision": decision,
                    "actor": "local-user",
                    "evidence": summary,
                },
            )

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
