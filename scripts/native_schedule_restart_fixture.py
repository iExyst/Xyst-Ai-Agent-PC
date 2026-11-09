"""Seed a missed one-time schedule for the packaged cold-restart acceptance."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from xaa.storage.runs import RunStore
from xaa.storage.schedules import ScheduleStore


def seed(database: Path, report_path: Path) -> dict[str, Any]:
    store = RunStore(database)
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    start_at = now - timedelta(minutes=2)
    schedule = ScheduleStore(store).create(
        objective="Persist a missed schedule across a cold desktop restart.",
        acceptance_criteria=["Record the missed occurrence once without dispatch."],
        workspace=None,
        model="diagnostic-no-inference",
        start_at=start_at.replace(tzinfo=None),
        frequency="once",
        timezone="UTC",
        missed_run_policy="skip",
        max_iterations=1,
        max_seconds=30,
    )
    report: dict[str, Any] = {
        "schedule_id": schedule["id"],
        "objective": schedule["objective"],
        "initial_next_at": schedule["recurrence"]["next_at"],
        "missed_run_policy": schedule["missed_run_policy"],
        "database": str(database),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(seed(args.database, args.report)))


if __name__ == "__main__":
    main()
