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


def main() -> None:
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
    (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not passed:
        raise RuntimeError("Scheduled restart dispatch was not exactly once.")


if __name__ == "__main__":
    main()
