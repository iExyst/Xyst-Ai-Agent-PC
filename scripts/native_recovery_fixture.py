"""Seed blocked workspace writes for the packaged desktop recovery acceptance."""

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
    original = b"installed recovery original contents\n"
    original_sha256 = hashlib.sha256(original).hexdigest()
    edit_path = workspace / "edit.txt"
    create_path = workspace / "partial-create.txt"
    delete_path = workspace / "deleted.txt"
    edit_path.write_bytes(original)
    create_path.write_bytes(b"interrupted partial create")
    delete_path.write_bytes(original)

    store = RunStore(database_path)
    run = store.create_run(
        objective="Review and recover interrupted workspace writes",
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
    calls: list[dict[str, Any]] = [
        {
            "call_id": "installed-recovery-edit",
            "tool": "workspace.write_file",
            "arguments": {
                "path": edit_path.name,
                "content": "post-crash edit content that must not be written",
                "expected_sha256": original_sha256,
            },
        },
        {
            "call_id": "installed-recovery-create",
            "tool": "workspace.create_file",
            "arguments": {
                "path": create_path.name,
                "content": "post-crash create content that must not be written",
            },
        },
        {
            "call_id": "installed-recovery-delete",
            "tool": "workspace.delete_file",
            "arguments": {"path": delete_path.name, "expected_sha256": original_sha256},
        },
    ]
    for call in calls:
        store.record_tool_intent(
            run_id,
            owner,
            call_id=call["call_id"],
            tool=call["tool"],
            version="1",
            arguments=call["arguments"],
            idempotency_key=call["call_id"],
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
        "objective": "Review and recover interrupted workspace writes",
        "run_id": run_id,
        "calls": [{"call_id": call["call_id"], "tool": call["tool"]} for call in calls],
        "workspace": str(workspace),
        "edit_path": str(edit_path),
        "create_path": str(create_path),
        "delete_path": str(delete_path),
        "original_sha256": original_sha256,
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
