"""Frozen private command capture and scope refusal without WSL or model requirements."""

import argparse
import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from typing import cast
from unittest.mock import patch
from uuid import uuid4

from xaa.runtime.owned_transport import request_process
from xaa.runtime.snapshot_worker import SnapshotRequest
from xaa.runtime.states import RunState
from xaa.runtime.task_graph import TaskDefinition
from xaa.runtime.tool_worker import FilesystemOperation, execute_filesystem
from xaa.runtime.workspace_snapshot import MAX_WIRE_BYTES, WorkspaceSnapshot
from xaa.storage.commands import CommandJournal
from xaa.storage.runs import RunStore, TaskReadScope
from xaa.storage.tasks import TaskStore


async def diagnose(engine: Path) -> None:
    engine = engine.resolve(strict=True)
    root = (Path(".acceptance") / str(uuid4())).resolve()
    project = root / "project"
    project.mkdir(parents=True)
    name = "\u96ea.txt"
    (project / name).write_bytes(b"original")
    store = RunStore(root / "data" / "db.sqlite3")
    run_id = store.create_run(
        objective="Frozen private capture",
        acceptance_criteria=[],
        workspace=str(project),
        mode="workspace",
        model=None,
        command_grant={"distribution": "Fixture-not-launched", "profile_sha256": "a" * 64},
    )["id"]
    owner = store.acquire_lease(run_id, "capture-fixture", seconds=180)
    assert owner is not None
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        owner,
        [
            TaskDefinition(
                id=task,
                objective="Capture private tree",
                permissions=("workspace.read", "workspace.write", "workspace.command"),
            )
            for task in ("a", "b")
        ],
    )
    scopes: list[TaskReadScope] = []
    for task in ("a", "b"):
        claim = tasks.claim_ready(run_id, owner)
        assert claim is not None
        scope = TaskReadScope(run_id, owner, task, claim["token"])
        scopes.append(scope)
        for operation, arguments in [
            ("branch_init", {}),
            (
                "branch_write",
                {
                    "path": name,
                    "content": task,
                    "expected_sha256": hashlib.sha256(b"original").hexdigest(),
                },
            ),
        ]:
            await execute_filesystem(
                store,
                run_id,
                owner,
                task + operation,
                cast(FilesystemOperation, operation),
                arguments,
                idempotency_key=task + operation,
                internal=True,
                task_id=task,
                task_token=scope.task_token,
            )
    store.transition(run_id, RunState.PLANNING, owner=owner)
    store.transition(run_id, RunState.RUNNING, owner=owner)
    CommandJournal(store, task_scope=scopes[0]).reserve(
        run_id, owner, "capture", {"argv": ["not-launched"]}, idempotency_key="capture"
    )
    request = SnapshotRequest(
        database=str(store.path),
        run_id=run_id,
        owner=owner,
        call_id="capture",
        task_id="a",
        task_token=scopes[0].task_token,
    )
    environment = dict(os.environ)
    environment["PATH"] = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32")
    for key in ("PYTHONPATH", "VIRTUAL_ENV"):
        environment.pop(key, None)

    async def capture(payload: SnapshotRequest) -> bytes:
        with patch.dict(os.environ, environment, clear=True):
            return await request_process(
                [str(engine), "--snapshot-worker"],
                payload.model_dump_json().encode(),
                timeout=10,
                output_limit=MAX_WIRE_BYTES,
            )

    snapshot = WorkspaceSnapshot.from_bytes(await capture(request))
    assert [(item.path, item.content) for item in snapshot.files] == [(name, b"a")]
    refusals = 0
    for invalid in [
        request.model_copy(update={"task_id": None, "task_token": None}),
        request.model_copy(update={"task_id": "b", "task_token": scopes[1].task_token}),
    ]:
        try:
            await capture(invalid)
        except RuntimeError:
            refusals += 1
        else:
            raise AssertionError("Frozen worker accepted missing/wrong task scope")
    with store.connection() as connection:
        connection.execute(
            "UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00' WHERE task_id=?",
            (run_id + ":a",),
        )
    try:
        await capture(request)
    except RuntimeError:
        refusals += 1
    else:
        raise AssertionError("Frozen worker accepted expired scope")
    assert refusals == 3 and (project / name).read_bytes() == b"original"
    assert not any(event["event_type"] == "tool.executing" for event in store.list_events(run_id))
    report = {
        "passed": True,
        "engine_sha256": hashlib.sha256(engine.read_bytes()).hexdigest(),
        "private_unicode_snapshot_verified": True,
        "wrong_missing_expired_task_refused": True,
        "original_unchanged": True,
        "snapshot_only_no_remote_launch": True,
        "scope": "Actual frozen snapshot IPC with System32-only PATH; no WSL/model/integration execution claim",
        "observed_at_epoch": time.time(),
    }
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Frozen private command snapshot evidence: {root}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(diagnose(args.engine))
