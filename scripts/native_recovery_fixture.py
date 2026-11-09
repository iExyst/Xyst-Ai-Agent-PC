"""Seed a blocked workspace edit for the packaged desktop recovery acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from xaa.runtime.states import RunState
from xaa.storage.runs import RunStore


def seed(database_path: Path, workspace: Path, report_path: Path) -> dict[str, Any]:
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / "fixture.txt"
    original = b"installed recovery original contents\n"
    target.write_bytes(original)

    store = RunStore(database_path)
    run = store.create_run(
        objective="Review and recover an interrupted workspace edit",
        acceptance_criteria=[],
        workspace=str(workspace),
        mode="workspace",
        model=None,
    )
    run_id_value = run.get("id")
    if not isinstance(run_id_value, str):
        raise TypeError("The recovery fixture run has no valid ID.")
    run_id = run_id_value
    owner = store.acquire_lease(run_id, "native-installed-recovery-fixture")
    if owner is None:
        raise RuntimeError("Could not lease the disposable recovery fixture run.")
    store.transition(run_id, RunState.PLANNING, owner=owner)
    store.transition(run_id, RunState.RUNNING, owner=owner)
    call_id = "installed-recovery-edit"
    store.record_tool_intent(
        run_id,
        owner,
        call_id=call_id,
        tool="workspace.write_file",
        version="1",
        arguments={
            "path": target.name,
            "content": "post-crash content that must not be written",
            "expected_sha256": hashlib.sha256(original).hexdigest(),
        },
        idempotency_key=call_id,
    )
    store.transition(
        run_id,
        RunState.BLOCKED,
        reason="tool_reconciliation_required",
        owner=owner,
    )
    store.release_lease(run_id, owner)
    saved_run = store.get_run(run_id)
    if saved_run is None:
        raise RuntimeError("The recovery fixture run was not persisted.")
    report: dict[str, Any] = {
        "objective": "Review and recover an interrupted workspace edit",
        "run_id": run_id,
        "call_id": call_id,
        "workspace": str(workspace),
        "path": str(target),
        "original_sha256": hashlib.sha256(original).hexdigest(),
        "inference_attempts_at_seed": store.inference_attempts(run_id),
        "state_at_seed": saved_run["state"],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(seed(arguments.database, arguments.workspace, arguments.report)))


if __name__ == "__main__":
    main()
