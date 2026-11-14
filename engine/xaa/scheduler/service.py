"""Poll durable local-time schedules and submit runs to the shared supervisor."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from xaa.storage.runs import RunStore
from xaa.storage.schedules import (
    OccurrenceOutcome,
    ScheduleStore,
    advance_local,
    local_instant,
    parse_local_time,
)

logger = logging.getLogger(__name__)
MISSED_GRACE = timedelta(seconds=60)


def _occurrence_at(start: datetime, frequency: str, index: int) -> datetime:
    """Calculate a recurrence wall time without walking missed occurrences."""
    if frequency == "daily":
        return start + timedelta(days=index)
    if frequency == "weekly":
        return start + timedelta(weeks=index)
    if frequency == "once":
        return start
    raise ValueError("Unsupported saved schedule frequency.")


def due_occurrence(
    recurrence: dict[str, Any], timezone: str, now: datetime
) -> tuple[datetime, datetime | None, int] | None:
    """Return the latest due local occurrence, its UTC instant and missed count."""
    start = parse_local_time(str(recurrence["next_at"]))
    frequency = recurrence["frequency"]
    zone = ZoneInfo(timezone)
    local_now = now.astimezone(zone).replace(tzinfo=None, second=0, microsecond=0)
    current_instant = now.astimezone(UTC)
    if start > local_now:
        if start.date() != local_now.date():
            return None
        start_instant = local_instant(start, timezone)
        if start_instant is None or start_instant > current_instant:
            return None
    if frequency == "once":
        count = 1
    elif frequency == "daily":
        count = (local_now.date() - start.date()).days + 1
    elif frequency == "weekly":
        count = (local_now.date() - start.date()).days // 7 + 1
    else:
        raise ValueError("Unsupported saved schedule frequency.")
    latest = _occurrence_at(start, frequency, count - 1)
    instant = local_instant(latest, timezone)
    if latest > local_now and (instant is None or instant > current_instant):
        count -= 1
        if count == 0:
            return None
        latest = _occurrence_at(start, frequency, count - 1)
        instant = local_instant(latest, timezone)
    return latest, instant, count


class ScheduleService:
    def __init__(self, store: RunStore, submit: Callable[[str], None]) -> None:
        self.runs = store
        self.schedules = ScheduleStore(store)
        self.submit = submit
        self.task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self.run(), name="schedule-service")

    async def close(self) -> None:
        task, self.task = self.task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def run(self) -> None:
        while True:
            try:
                self.tick()
            except (sqlite3.Error, ValueError, TypeError, KeyError, OSError) as exc:
                logger.error("Schedule poll failed (%s)", type(exc).__name__)
            await asyncio.sleep(2)

    def tick(self, now: datetime | None = None) -> list[str]:
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("Schedule clock must be timezone-aware.")
        current = current.astimezone(UTC)
        self.schedules.update_execution_states()
        run_ids: list[str] = []
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute("SELECT * FROM schedules WHERE enabled=1").fetchall()
            for schedule in rows:
                recurrence = json.loads(schedule["recurrence"])
                due = due_occurrence(recurrence, str(schedule["timezone"]), current)
                if due is None:
                    continue
                occurrence, instant, count = due
                frequency = recurrence["frequency"]
                next_at = advance_local(occurrence, frequency) if frequency != "once" else None
                if instant is None:
                    outcome: OccurrenceOutcome = "dst_skipped"
                else:
                    late_by = current - instant
                    policy = str(schedule["missed_run_policy"])
                    outcome = (
                        "missed"
                        if policy == "skip" and (late_by > MISSED_GRACE or count > 1)
                        else "run"
                    )
                occurrence_id = f"{schedule['id']}:{occurrence.isoformat(timespec='minutes')}"
                run_id = self.schedules.claim_occurrence(
                    connection,
                    schedule=schedule,
                    recurrence=recurrence,
                    occurrence_id=occurrence_id,
                    outcome=outcome,
                    next_at=next_at,
                )
                if run_id is not None:
                    run_ids.append(run_id)
        for run_id in run_ids:
            self.submit(run_id)
        return run_ids
