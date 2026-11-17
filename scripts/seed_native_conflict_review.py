"""Seed a deterministic pair of private specialist edits for native review acceptance."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from xaa.runtime.states import RunState
from xaa.runtime.task_graph import TaskDefinition
from xaa.storage.runs import RunStore
from xaa.storage.task_workspaces import TaskWorkspaces
from xaa.storage.tasks import TaskStore


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: seed_native_conflict_review.py ACCEPTANCE_ROOT")
    root = Path(sys.argv[1]).resolve()
    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    original = b"def answer():\n    return 'original'\n"
    (workspace / "shared.py").write_bytes(original)
    original_hash = hashlib.sha256(original).hexdigest()

    store = RunStore()
    objective = "Native specialist conflict review fixture"
    run_id = store.create_run(
        objective=objective,
        acceptance_criteria=[],
        workspace=str(workspace),
        mode="workspace",
        model=None,
        max_iterations=8,
        max_seconds=300,
    )["id"]
    owner = store.acquire_lease(run_id, "native-conflict-review-seed", seconds=300)
    if owner is None:
        raise SystemExit("could not acquire native conflict fixture run lease")
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        owner,
        [
            TaskDefinition(
                id=name,
                objective=f"Prepare the {name} implementation in a private copy.",
                acceptance_criteria=(f"The {name} implementation matches the saved evidence.",),
                expected_outputs=(f"{name} implementation",),
                role="developer",
                permissions=("workspace.read", "workspace.write"),
                max_iterations=1,
                max_seconds=90,
            )
            for name in ("alpha", "beta")
        ],
    )
    workspaces = TaskWorkspaces(store)
    for name in ("alpha", "beta"):
        claimed = tasks.claim_ready(run_id, owner)
        if claimed is None or claimed["task"]["local_id"] != name:
            raise SystemExit(f"could not claim the expected {name} fixture specialist")
        task_token = claimed["token"]
        workspaces.initialize(run_id, owner, name, task_token)
        read_call = f"native-read-{name}"
        read = workspaces.operate(
            run_id,
            owner,
            name,
            task_token,
            read_call,
            "branch_read",
            {"path": "shared.py"},
            idempotency_key=read_call,
        )
        tasks.record_read_worker_event(
            run_id,
            owner,
            name,
            task_token,
            "task.tool.result",
            {
                "task_id": name,
                "call_id": f"native-model-read-{name}",
                "journal_id": read_call,
                "result": read,
            },
        )
        write_call = f"native-write-{name}"
        content = f"def answer():\n    return '{name}'\n"
        workspaces.operate(
            run_id,
            owner,
            name,
            task_token,
            write_call,
            "branch_write",
            {
                "path": "shared.py",
                "expected_sha256": original_hash,
                "content": content,
            },
            idempotency_key=write_call,
        )
        tasks.record_outcome(
            run_id,
            owner,
            name,
            task_token,
            state="verifying",
            result={"candidate": f"Private {name} implementation."},
        )
    for state in (RunState.PLANNING, RunState.RUNNING, RunState.VERIFYING):
        store.transition(run_id, state, reason="native_fixture_ready", owner=owner)
    store.release_lease(run_id, owner)
    if (workspace / "shared.py").read_bytes() != original:
        raise SystemExit("private specialist proposals changed the project before owner review")
    print(
        json.dumps(
            {
                "run_id": run_id,
                "objective": objective,
                "workspace": str(workspace),
                "original_sha256": original_hash,
                "proposed_sha256": {
                    name: hashlib.sha256(
                        f"def answer():\n    return '{name}'\n".encode()
                    ).hexdigest()
                    for name in ("alpha", "beta")
                },
                "model_inference_requested": False,
            }
        )
    )


if __name__ == "__main__":
    main()
