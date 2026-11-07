"""Seed one named, read-only specialist in an isolated native restart profile."""

from __future__ import annotations

import sys
from pathlib import Path

from xaa.runtime.states import RunState
from xaa.runtime.task_graph import TaskDefinition
from xaa.storage.runs import RunStore
from xaa.storage.specialist_profiles import SpecialistProfileStore
from xaa.storage.tasks import TaskStore


def main() -> None:
    if len(sys.argv) != 8:
        raise SystemExit(
            "usage: seed_native_specialist_restart.py DB RUN_ID WORKSPACE MODEL "
            "IDENTITY INSTRUCTIONS FIXTURE"
        )
    database, run_id, workspace, model, identity, instructions, fixture = sys.argv[1:]
    store = RunStore(Path(database))
    run = store.get_run(run_id)
    if run is None or run["model"] is not None or run["state"] != RunState.QUEUED:
        raise SystemExit("fixture must be a queued, model-free run in the isolated profile")
    if run["mode"] != "workspace" or run["workspace"] != str(Path(workspace).resolve()):
        raise SystemExit("fixture run must be workspace-scoped to the disposable directory")
    owner = store.acquire_lease(run_id, "native-specialist-restart-seed", seconds=120)
    if owner is None:
        raise SystemExit("could not acquire the isolated parent run lease")
    profiles = SpecialistProfileStore(store)
    try:
        profiles.set_display_name("researcher", identity)
        profiles.set_route("researcher", model)
        profiles.set_instructions("researcher", instructions)
        with store.connection() as connection:
            changed = connection.execute(
                "UPDATE runs SET model=? WHERE id=? AND model IS NULL AND lease_owner=?",
                (model, run_id, owner),
            ).rowcount
        if changed != 1:
            raise RuntimeError("could not attach the discovered fixture model to the run")
        TaskStore(store).save_plan(
            run_id,
            owner,
            [
                TaskDefinition(
                    id="reader",
                    objective=(
                        f"Read {fixture} exactly once with workspace_read_file. "
                        "Report the complete exact contents in the candidate."
                    ),
                    acceptance_criteria=(
                        "Return the exact file contents read from the workspace.",
                    ),
                    role="researcher",
                    permissions=("workspace.read",),
                    max_iterations=4,
                    max_seconds=120,
                )
            ],
        )
        store.transition(
            run_id, RunState.PLANNING, reason="native_fixture_plan_seeded", owner=owner
        )
        store.transition(run_id, RunState.RUNNING, reason="native_fixture_plan_seeded", owner=owner)
        store.transition(
            run_id, RunState.VERIFYING, reason="native_fixture_plan_seeded", owner=owner
        )
    finally:
        store.release_lease(run_id, owner)
    print("named researcher identity, route, instructions, and read-only task saved")


if __name__ == "__main__":
    main()
