"""Seed an isolated final-review run for frozen native validation acceptance."""

import hashlib
import json
import sys
from pathlib import Path

from xaa.runtime.executor import WorkspaceExecutor
from xaa.runtime.states import RunState
from xaa.storage.runs import RunStore


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    project = root / "project"
    data = root / "data"
    project.mkdir(parents=True, exist_ok=False)
    old = b"def answer():\n    return 41\n"
    (project / "answer.py").write_bytes(old)
    store = RunStore(data / "XAA" / "xaa.sqlite3")
    grant = {
        "backend": "wsl-bubblewrap-v1",
        "distribution": "Ubuntu-24.04",
        "profile_sha256": "6cfb2f0b92851ba2fc38cf7f1f04a59af57f310f7cadbe474cfc513e83ebc712",
        "max_seconds": 20,
    }
    run = store.create_run(
        objective="Validate the integrated answer.py project",
        acceptance_criteria=["answer() returns 42"],
        workspace=str(project),
        mode="workspace",
        model=None,
        command_grant=grant,
    )
    run_id = run["id"]
    owner = store.acquire_lease(run_id, "native-validation-fixture", seconds=60)
    if owner is None:
        raise RuntimeError("Could not acquire the isolated fixture run.")
    store.transition(run_id, RunState.PLANNING, owner=owner)
    store.transition(run_id, RunState.RUNNING, owner=owner)
    WorkspaceExecutor(store).write_file(
        run_id,
        owner,
        "fixture-edit",
        {
            "path": "answer.py",
            "content": "def answer():\n    return 42\n",
            "expected_sha256": hashlib.sha256(old).hexdigest(),
        },
        idempotency_key="fixture-edit",
    )
    store.append_worker_event(
        run_id,
        owner,
        "model.message",
        {
            "message": {
                "role": "assistant",
                "content": "answer() now returns 42. The integrated project is ready for validation.",
            }
        },
    )
    store.transition(run_id, RunState.VERIFYING, owner=owner)
    store.release_lease(run_id, owner)
    print(json.dumps({"run_id": run_id, "workspace": str(project), "data": str(data)}))


if __name__ == "__main__":
    main()
