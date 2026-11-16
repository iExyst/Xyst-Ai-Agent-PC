"""Seed a deterministic three-task plan for native desktop progress acceptance."""

from __future__ import annotations

import sys

from xaa.runtime.states import RunState
from xaa.runtime.task_graph import TaskDefinition
from xaa.storage.runs import RunStore
from xaa.storage.tasks import TaskStore


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: seed_native_plan_progress.py RUN_ID")
    run_id = sys.argv[1]
    store = RunStore()
    run = store.get_run(run_id)
    if run is None or run["model"] is not None or run["state"] != RunState.QUEUED:
        raise SystemExit("fixture must be a queued, model-free run in the isolated database")
    owner = store.acquire_lease(run_id, "native-plan-progress-smoke", seconds=60)
    if owner is None:
        raise SystemExit("could not acquire fixture run lease")
    try:
        store.transition(run_id, RunState.PLANNING, reason="native_fixture_seed", owner=owner)
        TaskStore(store).save_plan(
            run_id,
            owner,
            [
                TaskDefinition(
                    id="inspect",
                    objective="Inspect the primary fixture without making changes.",
                    acceptance_criteria=("Record the observed fixture state.",),
                    role="researcher",
                    permissions=("workspace.read",),
                    max_iterations=1,
                    max_seconds=10,
                ),
                TaskDefinition(
                    id="inventory",
                    objective="Inventory the independent fixture inputs.",
                    acceptance_criteria=("Record the available inputs.",),
                    role="researcher",
                    permissions=("workspace.read",),
                    max_iterations=1,
                    max_seconds=10,
                ),
                TaskDefinition(
                    id="review",
                    objective="Review the inspection evidence.",
                    acceptance_criteria=("Check the saved evidence.",),
                    dependencies=("inspect",),
                    role="reviewer",
                    permissions=("workspace.read",),
                    max_iterations=1,
                    max_seconds=10,
                ),
            ],
        )
        store.transition(run_id, RunState.RUNNING, reason="native_fixture_plan_ready", owner=owner)
        store.transition(
            run_id, RunState.VERIFYING, reason="native_fixture_plan_saved", owner=owner
        )
    finally:
        store.release_lease(run_id, owner)
    print("validated native plan progress fixture saved")


if __name__ == "__main__":
    main()
