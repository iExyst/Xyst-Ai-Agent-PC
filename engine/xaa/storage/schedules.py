"""Durable schedules and transactionally claimed run occurrences."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from xaa.runtime.states import RunState
from xaa.storage.runs import RunStore, utc_now

ScheduleFrequency = Literal["once", "daily", "weekly"]
OccurrenceOutcome = Literal["run", "missed", "overlap_skipped", "dst_skipped"]
MAX_SCHEDULE_DELIVERY_ATTEMPTS = 3
_QUIET_HOURS = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]-(?:[01][0-9]|2[0-3]):[0-5][0-9]$")


def validate_quiet_hours(value: str | None) -> str | None:
    """Validate a local daily notification quiet window in HH:MM-HH:MM form."""
    if value is None:
        return None
    if not _QUIET_HOURS.fullmatch(value):
        raise ValueError("Quiet hours must use HH:MM-HH:MM in the schedule timezone.")
    start, end = value.split("-")
    if start == end:
        raise ValueError("Quiet hours must have different start and end times.")
    return value


def quiet_hours_active(value: str | None, timezone: str, now: datetime) -> bool:
    """Whether a notification is currently inside its local quiet window."""
    if value is None:
        return False
    validate_quiet_hours(value)
    local = now.astimezone(ZoneInfo(timezone))
    minute = local.hour * 60 + local.minute
    start_text, end_text = value.split("-")
    start_hour, start_minute = (int(part) for part in start_text.split(":"))
    end_hour, end_minute = (int(part) for part in end_text.split(":"))
    start = start_hour * 60 + start_minute
    end = end_hour * 60 + end_minute
    return start <= minute < end if start < end else minute >= start or minute < end


def parse_local_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Schedule time must be an ISO local date and time.") from exc
    if parsed.tzinfo is not None:
        raise ValueError("Schedule time must be local to its named timezone, without an offset.")
    return parsed.replace(second=0, microsecond=0)


def local_instant(value: datetime, timezone: str) -> datetime | None:
    """Resolve a local time, choosing the earlier occurrence when clocks move back.

    Nonexistent spring-forward wall times return None and are recorded as skipped.
    """
    zone = ZoneInfo(timezone)
    candidate = value.replace(tzinfo=zone, fold=0)
    roundtrip = candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
    return candidate.astimezone(UTC) if roundtrip == value else None


def advance_local(value: datetime, frequency: ScheduleFrequency) -> datetime:
    if frequency == "daily":
        from datetime import timedelta

        return value + timedelta(days=1)
    if frequency == "weekly":
        from datetime import timedelta

        return value + timedelta(weeks=1)
    return value


class ScheduleStore:
    def __init__(self, runs: RunStore) -> None:
        self.runs = runs

    def create(
        self,
        *,
        objective: str,
        acceptance_criteria: list[str],
        workspace: str | None,
        model: str,
        start_at: datetime,
        frequency: ScheduleFrequency,
        timezone: str,
        missed_run_policy: Literal["skip", "run_once"],
        max_iterations: int,
        max_seconds: int,
        mode: Literal["observe", "authorized_automation"] = "observe",
        delivery_destination: str | None = None,
        failure_notification_destination: str | None = None,
        quiet_hours: str | None = None,
    ) -> dict[str, Any]:
        ZoneInfo(timezone)
        if mode == "authorized_automation" and not workspace:
            raise ValueError("Authorized automation requires an explicitly selected workspace.")
        quiet_hours = validate_quiet_hours(quiet_hours)
        if local_instant(start_at, timezone) is None:
            raise ValueError("This local time does not exist because of a daylight-saving change.")
        identity = str(uuid.uuid4())
        now = utc_now()
        recurrence = {
            "frequency": frequency,
            "next_at": start_at.isoformat(timespec="minutes"),
            "acceptance_criteria": acceptance_criteria,
            "workspace": workspace,
            "model": model,
            "max_iterations": max_iterations,
            "max_seconds": max_seconds,
        }
        with self.runs.connection() as connection:
            connection.execute(
                "INSERT INTO schedules (id,objective,recurrence,mode,timezone,missed_run_policy,overlap_policy,quiet_hours,delivery_destination,failure_notification_destination,enabled,created_at,updated_at) VALUES (?,?,?,?,?,?,'skip',?,?,?,1,?,?)",
                (
                    identity,
                    objective,
                    json.dumps(recurrence),
                    mode,
                    timezone,
                    missed_run_policy,
                    quiet_hours,
                    delivery_destination,
                    failure_notification_destination,
                    now,
                    now,
                ),
            )
        result = self.get(identity)
        assert result is not None
        return result

    def get(self, schedule_id: str) -> dict[str, Any] | None:
        with self.runs.connection() as connection:
            schedule = connection.execute(
                "SELECT * FROM schedules WHERE id=?", (schedule_id,)
            ).fetchone()
            if schedule is None:
                return None
            executions = connection.execute(
                "SELECT e.id,e.occurrence_id,e.run_id,e.state,e.delivery_evidence,e.created_at,e.completed_at,CASE WHEN d.external_identity IS NOT NULL THEN 'telegram:'||d.account_id||':'||d.external_identity ELSE NULL END AS delivery_destination,d.attempt_count AS delivery_attempt_count,d.next_attempt_at AS delivery_next_attempt_at FROM schedule_executions e LEFT JOIN schedule_deliveries d ON d.execution_id=e.id WHERE e.schedule_id=? AND (e.id IN (SELECT id FROM schedule_executions WHERE schedule_id=? ORDER BY created_at DESC LIMIT 20) OR e.delivery_evidence='unknown') ORDER BY e.created_at DESC",
                (schedule_id, schedule_id),
            ).fetchall()
        result = dict(schedule)
        result["recurrence"] = json.loads(result["recurrence"])
        result["enabled"] = bool(result["enabled"])
        result["executions"] = [dict(item) for item in executions]
        return result

    def reconcile_delivery(
        self,
        schedule_id: str,
        execution_id: str,
        account_id: str,
        resolution: Literal["delivered", "retry"],
    ) -> Literal["resolved", "not_unknown", "attempt_limit", "destination_revoked"]:
        state = "sent" if resolution == "delivered" else "pending"
        now = utc_now()
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            delivery = connection.execute(
                "SELECT d.external_identity,d.attempt_count FROM schedule_deliveries d JOIN schedule_executions e ON e.id=d.execution_id WHERE d.execution_id=? AND e.schedule_id=? AND d.account_id=? AND d.state='unknown'",
                (execution_id, schedule_id, account_id),
            ).fetchone()
            if delivery is None:
                return "not_unknown"
            if resolution == "retry":
                if int(delivery["attempt_count"]) >= MAX_SCHEDULE_DELIVERY_ATTEMPTS:
                    return "attempt_limit"
                linked = connection.execute(
                    "SELECT 1 FROM channel_bindings WHERE channel='telegram' AND account_id=? AND external_identity=? AND authorized=1",
                    (account_id, delivery["external_identity"]),
                ).fetchone()
                if linked is None:
                    return "destination_revoked"
            changed = connection.execute(
                "UPDATE schedule_deliveries SET state=?,next_attempt_at=NULL,updated_at=? WHERE execution_id=? AND account_id=? AND state='unknown'",
                (state, now, execution_id, account_id),
            ).rowcount
            if changed != 1:
                return "not_unknown"
            connection.execute(
                "UPDATE schedule_executions SET delivery_evidence=? WHERE id=?",
                (state, execution_id),
            )
            connection.execute(
                "INSERT INTO schedule_delivery_reconciliations (id,execution_id,resolution,resolved_at) VALUES (?,?,?,?)",
                (str(uuid.uuid4()), execution_id, resolution, now),
            )
        return "resolved"

    def list(self) -> list[dict[str, Any]]:
        with self.runs.connection() as connection:
            ids = [
                row[0]
                for row in connection.execute("SELECT id FROM schedules ORDER BY created_at DESC")
            ]
        return [schedule for identity in ids if (schedule := self.get(identity)) is not None]

    def delete(self, schedule_id: str) -> bool:
        with self.runs.connection() as connection:
            cursor = connection.execute(
                "UPDATE schedules SET enabled=0,updated_at=? WHERE id=?",
                (utc_now(), schedule_id),
            )
        return cursor.rowcount == 1

    def set_enabled(self, schedule_id: str, enabled: bool) -> bool:
        with self.runs.connection() as connection:
            cursor = connection.execute(
                "UPDATE schedules SET enabled=?,updated_at=? WHERE id=?",
                (int(enabled), utc_now(), schedule_id),
            )
        return cursor.rowcount == 1

    def set_delivery_destinations(
        self,
        schedule_id: str,
        delivery_destination: str | None,
        failure_notification_destination: str | None,
    ) -> bool:
        """Update destinations used by future occurrences; existing deliveries are snapshots."""
        with self.runs.connection() as connection:
            cursor = connection.execute(
                "UPDATE schedules SET delivery_destination=?,failure_notification_destination=?,updated_at=? WHERE id=?",
                (delivery_destination, failure_notification_destination, utc_now(), schedule_id),
            )
        return cursor.rowcount == 1

    def update_execution_states(self) -> None:
        with self.runs.connection() as connection:
            rows = connection.execute(
                "SELECT e.id,e.run_id,r.state FROM schedule_executions e JOIN runs r ON r.id=e.run_id WHERE e.state IN ('queued','running')"
            ).fetchall()
            for row in rows:
                run_state = str(row["state"])
                if run_state in {"queued", "planning", "running", "verifying"}:
                    state = "running" if run_state != "queued" else "queued"
                    completed_at = None
                elif run_state == "completed":
                    state, completed_at = "completed", utc_now()
                elif run_state in {"failed", "cancelled", "blocked"}:
                    state, completed_at = "failed", utc_now()
                else:
                    continue
                connection.execute(
                    "UPDATE schedule_executions SET state=?,completed_at=COALESCE(completed_at,?) WHERE id=?",
                    (state, completed_at, row["id"]),
                )

    def active_run_exists(self, connection: sqlite3.Connection, schedule_id: str) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM schedule_executions e JOIN runs r ON r.id=e.run_id WHERE e.schedule_id=? AND r.state IN ('queued','planning','running','verifying','waiting_for_approval','paused') LIMIT 1",
                (schedule_id,),
            ).fetchone()
            is not None
        )

    def claim_occurrence(
        self,
        connection: sqlite3.Connection,
        *,
        schedule: sqlite3.Row,
        recurrence: dict[str, Any],
        occurrence_id: str,
        outcome: OccurrenceOutcome,
        next_at: datetime | None,
    ) -> str | None:
        """Persist one unique occurrence and any resulting queued run atomically."""
        existing = connection.execute(
            "SELECT 1 FROM schedule_executions WHERE occurrence_id=?", (occurrence_id,)
        ).fetchone()
        if existing is not None:
            return None
        if outcome == "run" and self.active_run_exists(connection, str(schedule["id"])):
            outcome = "overlap_skipped"
        run_id: str | None = None
        state = "queued" if outcome == "run" else outcome
        if outcome == "run":
            run_id = str(uuid.uuid4())
            now = utc_now()
            connection.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,0,NULL,NULL)",
                (
                    run_id,
                    str(schedule["objective"]),
                    json.dumps(recurrence["acceptance_criteria"]),
                    recurrence.get("workspace"),
                    str(schedule["mode"]),
                    recurrence["model"],
                    RunState.QUEUED.value,
                    now,
                    now,
                ),
            )
            RunStore.record_event(
                connection,
                run_id,
                "run.queued",
                {"state": RunState.QUEUED.value, "schedule_id": schedule["id"]},
            )
            connection.execute(
                "INSERT INTO run_limits VALUES (?,?,?)",
                (run_id, recurrence["max_iterations"], recurrence["max_seconds"]),
            )
            connection.execute("INSERT INTO run_checks VALUES (?, '[]')", (run_id,))
        execution_id = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO schedule_executions (id,schedule_id,occurrence_id,run_id,state,delivery_evidence,created_at,completed_at) VALUES (?,?,?,?,?,NULL,?,?)",
            (
                execution_id,
                schedule["id"],
                occurrence_id,
                run_id,
                state,
                utc_now(),
                utc_now() if outcome != "run" else None,
            ),
        )
        destination = schedule["delivery_destination"]
        failure_destination = schedule["failure_notification_destination"]
        if outcome == "run" and (destination is not None or failure_destination is not None):

            def parse_destination(value: str | None) -> tuple[str, str] | None:
                if value is None:
                    return None
                parts = str(value).split(":")
                if (
                    len(parts) != 3
                    or parts[0] != "telegram"
                    or not all(part.isdecimal() for part in parts[1:])
                ):
                    raise ValueError("Saved schedule delivery destination is malformed.")
                return parts[1], parts[2]

            result_target = parse_destination(destination)
            failure_target = parse_destination(failure_destination)
            initial_target = result_target or failure_target
            assert initial_target is not None
            connection.execute(
                "INSERT INTO schedule_deliveries (execution_id,account_id,external_identity,chat_id,result_external_identity,result_chat_id,failure_external_identity,failure_chat_id,state,message_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?, 'pending',NULL,?,?)",
                (
                    execution_id,
                    initial_target[0],
                    initial_target[1],
                    initial_target[1],
                    result_target[1] if result_target else None,
                    result_target[1] if result_target else None,
                    failure_target[1] if failure_target else None,
                    failure_target[1] if failure_target else None,
                    utc_now(),
                    utc_now(),
                ),
            )
            connection.execute(
                "UPDATE schedule_executions SET delivery_evidence='pending' WHERE id=?",
                (execution_id,),
            )
        if next_at is None:
            connection.execute(
                "UPDATE schedules SET enabled=0,updated_at=? WHERE id=?",
                (utc_now(), schedule["id"]),
            )
        else:
            recurrence["next_at"] = next_at.isoformat(timespec="minutes")
            connection.execute(
                "UPDATE schedules SET recurrence=?,updated_at=? WHERE id=?",
                (json.dumps(recurrence), utc_now(), schedule["id"]),
            )
        return run_id
