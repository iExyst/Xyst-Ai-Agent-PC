"""Seed a validated read-only specialist graph in the isolated native smoke DB."""

from __future__ import annotations

import sys

from xaa.runtime.states import RunState
from xaa.runtime.task_graph import TaskDefinition
from xaa.storage.runs import RunStore
from xaa.storage.tasks import TaskStore


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("usage: seed_native_specialists.py RUN_ID MODEL_ID FIXTURE_NAME")
    store = RunStore()
    run_id = sys.argv[1]
    model_id = sys.argv[2]
    fixture_name = sys.argv[3]
    run = store.get_run(run_id)
    if run is None or run["model"] is not None or run["state"] != RunState.QUEUED:
        raise SystemExit("fixture must be a queued, model-free run in the isolated database")
    owner = store.acquire_lease(run_id, "native-specialist-smoke", seconds=120)
    if owner is None:
        raise SystemExit("could not acquire fixture run lease")
    try:
        store.transition(run_id, RunState.PLANNING, reason="native_fixture_seed", owner=owner)
        with store.connection() as connection:
            changed = connection.execute(
                "UPDATE runs SET model=? WHERE id=? AND model IS NULL AND lease_owner=?",
                (model_id, run_id, owner),
            ).rowcount
        if changed != 1:
            raise RuntimeError("could not attach the selected model to the isolated fixture run")
        TaskStore(store).save_plan(
            run_id,
            owner,
            [
                TaskDefinition(
                    id="inspect",
                    objective=f"Read {fixture_name} with workspace_read_file and report its exact full code.",
                    acceptance_criteria=("Report the exact code read from the fixture file.",),
                    role="researcher",
                    permissions=("workspace.read",),
                    max_iterations=2,
                    max_seconds=90,
                ),
                TaskDefinition(
                    id="review",
                    objective=(
                        f"Independently read {fixture_name} with workspace_read_file, then quote "
                        "its complete exact contents verbatim in your candidate. Do not summarize, "
                        "say only that it matches, or omit the literal code. Also compare the "
                        "inspect candidate with the file you read."
                    ),
                    acceptance_criteria=("Report the exact code read from the fixture file.",),
                    dependencies=("inspect",),
                    role="reviewer",
                    permissions=("workspace.read",),
                    max_iterations=3,
                    max_seconds=90,
                ),
            ],
        )
        store.transition(run_id, RunState.RUNNING, reason="native_fixture_plan_ready", owner=owner)
        store.transition(
            run_id, RunState.VERIFYING, reason="native_fixture_plan_saved", owner=owner
        )
    finally:
        store.release_lease(run_id, owner)
    print("validated native specialist fixture plan saved")


if __name__ == "__main__":
    main()
