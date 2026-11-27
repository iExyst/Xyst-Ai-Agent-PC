"""Verify a due schedule is recovered exactly once after a process restart."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from xaa.runtime.states import RunState
from xaa.runtime.supervisor import RunSupervisor
from xaa.scheduler.service import ScheduleService
from xaa.storage.runs import RunStore
from xaa.storage.schedules import ScheduleStore


class FixtureCoordinator:
    """Record recovered work and stop it without invoking a model provider."""

    def __init__(self, store: RunStore, dispatch_log: Path) -> None:
        self.store = store
        self.dispatch_log = dispatch_log
        self.dispatched = asyncio.Event()

    async def execute(self, run_id: str) -> None:
        owner = f"schedule-restart-diagnostic-{os.getpid()}"
        lease = self.store.acquire_lease(run_id, owner)
        if not lease:
            raise RuntimeError("Recovered schedule run could not acquire its lease.")
        self.store.transition(run_id, RunState.PLANNING, owner=lease)
        entry = {"pid": os.getpid(), "run_id": run_id}
        with self.dispatch_log.open("a", encoding="utf-8", newline="\n") as sink:
            sink.write(json.dumps(entry, sort_keys=True) + "\n")
            sink.flush()
            os.fsync(sink.fileno())
        self.store.transition(
            run_id, RunState.FAILED, reason="diagnostic_no_model_inference", owner=lease
        )
        self.store.release_lease(run_id, lease)
        self.dispatched.set()


async def recover_once(database: Path, dispatch_log: Path, *, expect_no_dispatch: bool) -> None:
    store = RunStore(database)
    coordinator = FixtureCoordinator(store, dispatch_log)
    supervisor = RunSupervisor(coordinator)  # type: ignore[arg-type]
    supervisor.start()
    try:
        try:
            await asyncio.wait_for(
                coordinator.dispatched.wait(), timeout=2 if expect_no_dispatch else 20
            )
        except TimeoutError:
            pass
        await asyncio.sleep(0)
    finally:
        await supervisor.close()


def prepare(root: Path) -> tuple[str, str]:
    store = RunStore(root / "runs.sqlite3")
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    schedule = ScheduleStore(store).create(
        objective="Recover this scheduled run after a process restart.",
        acceptance_criteria=["Dispatch the durable run once."],
        workspace=None,
        model="diagnostic-no-inference",
        start_at=now.replace(tzinfo=None),
        frequency="once",
        timezone="UTC",
        missed_run_policy="skip",
        max_iterations=1,
        max_seconds=30,
    )
    scheduled = ScheduleService(store, lambda _run_id: None).tick(now)
    if len(scheduled) != 1:
        raise RuntimeError("Could not persist exactly one due scheduled run.")
    run_id = scheduled[0]
    saved = ScheduleStore(store).get(schedule["id"])
    if saved is None or len(saved["executions"]) != 1:
        raise RuntimeError("The due schedule execution was not persisted exactly once.")
    if saved["executions"][0]["run_id"] != run_id:
        raise RuntimeError("Schedule execution references a different run.")
    return schedule["id"], run_id


def launch_recovery(
    database: Path, dispatch_log: Path, *, expect_no_dispatch: bool = False
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--recover",
            str(database),
            str(dispatch_log),
            *(["--expect-no-dispatch"] if expect_no_dispatch else []),
        ],
        capture_output=True,
        check=False,
        timeout=30,
        text=True,
    )


def launch_dst_tick(database: Path, instant: datetime) -> list[str]:
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--tick-dst",
            str(database),
            instant.isoformat(),
        ],
        capture_output=True,
        check=False,
        timeout=30,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"DST scheduler process failed ({result.returncode}): {result.stderr}")
    dispatched = json.loads(result.stdout)
    if not isinstance(dispatched, list) or any(not isinstance(item, str) for item in dispatched):
        raise RuntimeError("DST scheduler process returned an invalid dispatch list.")
    return dispatched


def main() -> None:
    if "--tick-dst" in sys.argv:
        index = sys.argv.index("--tick-dst")
        store = RunStore(Path(sys.argv[index + 1]))
        instant = datetime.fromisoformat(sys.argv[index + 2])
        dispatched = ScheduleService(store, lambda _run_id: None).tick(instant)
        print(json.dumps(dispatched))
        return

    if "--recover" in sys.argv:
        index = sys.argv.index("--recover")
        asyncio.run(
            recover_once(
                Path(sys.argv[index + 1]),
                Path(sys.argv[index + 2]),
                expect_no_dispatch="--expect-no-dispatch" in sys.argv,
            )
        )
        return

    root = (Path(".acceptance") / f"schedule-restart-{uuid4()}").resolve()
    root.mkdir(parents=True)
    database = root / "runs.sqlite3"
    dispatch_log = root / "dispatches.jsonl"
    dispatch_log.write_text("", encoding="utf-8")
    schedule_id, run_id = prepare(root)

    first = launch_recovery(database, dispatch_log)
    (root / "first-restart.stdout.log").write_text(first.stdout, encoding="utf-8")
    (root / "first-restart.stderr.log").write_text(first.stderr, encoding="utf-8")
    if first.returncode != 0:
        raise RuntimeError(f"First recovery process failed ({first.returncode}).")

    after_first = RunStore(database)
    first_dispatches = dispatch_log.read_text(encoding="utf-8").splitlines()
    run_after_first = after_first.get_run(run_id)
    if run_after_first is None or run_after_first["state"] != RunState.FAILED.value:
        raise RuntimeError("The fixture coordinator did not save a terminal run outcome.")

    second = launch_recovery(database, dispatch_log, expect_no_dispatch=True)
    (root / "second-restart.stdout.log").write_text(second.stdout, encoding="utf-8")
    (root / "second-restart.stderr.log").write_text(second.stderr, encoding="utf-8")
    if second.returncode != 0:
        raise RuntimeError(f"Second recovery process failed ({second.returncode}).")

    final_dispatches = dispatch_log.read_text(encoding="utf-8").splitlines()
    with after_first.connection() as connection:
        execution_count = int(
            connection.execute(
                "SELECT count(*) FROM schedule_executions WHERE schedule_id=? AND run_id=?",
                (schedule_id, run_id),
            ).fetchone()[0]
        )
    entries: list[dict[str, Any]] = [json.loads(line) for line in final_dispatches]
    passed = (
        len(first_dispatches) == 1
        and first_dispatches == final_dispatches
        and len(entries) == 1
        and entries[0]["run_id"] == run_id
        and entries[0]["pid"] != os.getpid()
        and execution_count == 1
    )
    report = {
        "passed": passed,
        "recovery_processes": 2,
        "scheduled_run_id": run_id,
        "schedule_execution_count": execution_count,
        "dispatch_count_after_first_restart": len(first_dispatches),
        "dispatch_count_after_second_restart": len(final_dispatches),
        "terminal_state_after_first_restart": run_after_first["state"],
        "model_inference_requested": False,
        "evidence": str(root),
    }
    dst_database = root / "dst-fall-back.sqlite3"
    dst_store = RunStore(dst_database)
    dst_schedule = ScheduleStore(dst_store).create(
        objective="Run once at the fall-back wall time.",
        acceptance_criteria=["Do not duplicate the repeated wall time after restart."],
        workspace=None,
        model="diagnostic-no-inference",
        start_at=datetime(2025, 11, 2, 1, 30),
        frequency="daily",
        timezone="America/New_York",
        missed_run_policy="run_once",
        max_iterations=1,
        max_seconds=30,
    )
    first_fall_back = launch_dst_tick(dst_database, datetime(2025, 11, 2, 5, 45, tzinfo=UTC))
    repeated_hour_after_restart = launch_dst_tick(
        dst_database, datetime(2025, 11, 2, 6, 0, tzinfo=UTC)
    )
    dst_saved = ScheduleStore(RunStore(dst_database)).get(dst_schedule["id"])
    dst_passed = (
        len(first_fall_back) == 1
        and repeated_hour_after_restart == []
        and dst_saved is not None
        and len(dst_saved["executions"]) == 1
        and dst_saved["recurrence"]["next_at"] == "2025-11-03T01:30"
    )
    report["dst_fall_back_process_restart"] = {
        "passed": dst_passed,
        "timezone": "America/New_York",
        "first_0130_utc": "2025-11-02T05:45:00+00:00",
        "repeated_0100_utc_after_restart": "2025-11-02T06:00:00+00:00",
        "dispatches_before_restart": len(first_fall_back),
        "dispatches_after_restart": len(repeated_hour_after_restart),
        "execution_count": len(dst_saved["executions"]) if dst_saved is not None else None,
        "next_local_occurrence": (
            dst_saved["recurrence"]["next_at"] if dst_saved is not None else None
        ),
    }
    report["passed"] = passed and dst_passed
    (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not report["passed"]:
        raise RuntimeError("Scheduled recovery or fall-back restart was not exactly once.")


if __name__ == "__main__":
    main()
