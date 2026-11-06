"""Exercise private proposal and explicitly authorized deletion in the frozen worker."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from xaa.runtime.owned_transport import request_process
from xaa.runtime.states import RunState
from xaa.runtime.task_graph import TaskDefinition
from xaa.runtime.tool_worker import WorkerResponse
from xaa.storage.runs import RunStore
from xaa.storage.tasks import TaskStore


async def diagnose(engine: Path) -> None:
    engine = engine.resolve(strict=True)
    root = (Path(".acceptance") / str(uuid4())).resolve()
    project = root / "project"
    project.mkdir(parents=True)
    target = project / "remove.txt"
    target.write_bytes(b"reviewed deletion fixture\n")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    store = RunStore(root / "data" / "db.sqlite3")
    run_id = store.create_run(
        objective="Propose and review one private deletion",
        acceptance_criteria=[],
        workspace=str(project),
        mode="workspace",
        model=None,
    )["id"]
    owner = store.acquire_lease(run_id, "frozen-deletion-fixture", seconds=180)
    assert owner is not None
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        owner,
        [
            TaskDefinition(
                id="deleter",
                objective="fixture",
                permissions=("workspace.read", "workspace.write"),
            )
        ],
    )
    claim = tasks.claim_ready(run_id, owner)
    assert claim is not None
    report: dict[str, Any] = {
        "passed": False,
        "engine_sha256": hashlib.sha256(engine.read_bytes()).hexdigest(),
        "scope": "Frozen --tool-worker private deletion proposal and explicit saved user authorization; no model/UI claim",
    }
    saved = {key: os.environ.get(key) for key in ("PATH", "PYTHONPATH", "VIRTUAL_ENV")}
    try:
        os.environ["PATH"] = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32")
        os.environ.pop("PYTHONPATH", None)
        os.environ.pop("VIRTUAL_ENV", None)

        async def send(
            call_id: str,
            operation: str,
            arguments: dict[str, Any],
            *,
            task_scope: bool = False,
        ) -> dict[str, Any]:
            payload = {
                "database": str(store.path),
                "run_id": run_id,
                "token": owner,
                "call_id": call_id,
                "operation": operation,
                "arguments": arguments,
                "idempotency_key": call_id,
                "internal": True,
            }
            if task_scope:
                payload.update(task_id="deleter", task_token=claim["token"])
            raw = await request_process(
                [str(engine), "--tool-worker"],
                json.dumps(payload).encode(),
                timeout=20,
                output_limit=65536,
            )
            response = WorkerResponse.model_validate_json(raw)
            assert response.ok, response.error
            return response.result

        captured = await send("delete-init", "branch_init", {}, task_scope=True)
        proposed = await send(
            "delete-proposal",
            "branch_delete",
            {"path": "remove.txt", "expected_sha256": digest},
            task_scope=True,
        )
        assert captured["effect"] == "private_copy_only" and proposed["deleted"]
        assert target.read_bytes() == b"reviewed deletion fixture\n"
        with store.connection() as connection:
            connection.execute(
                "UPDATE tasks SET status='verifying' WHERE id=?", (run_id + ":deleter",)
            )
        for state in (RunState.PLANNING, RunState.RUNNING, RunState.VERIFYING):
            store.transition(run_id, state, owner=owner)
        call_id = "authorized-delete"
        with store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            store.require_lease(connection, run_id, owner)
            store.record_event(
                connection,
                run_id,
                "task.integration.authorized",
                {
                    "task_id": "deleter",
                    "actor": "local-user",
                    "owner_sha256": hashlib.sha256(owner.encode()).hexdigest(),
                    "files": [
                        {
                            "path": "remove.txt",
                            "current_sha256": digest,
                            "proposed_sha256": None,
                            "choice": "proposed",
                        }
                    ],
                    "writes": [
                        {
                            "path": "remove.txt",
                            "current_sha256": digest,
                            "proposed_sha256": None,
                            "choice": "proposed",
                            "action": "delete",
                            "call_id": call_id,
                        }
                    ],
                },
            )
        result = await send(
            call_id,
            "integrate",
            {"task_id": "deleter", "path": "remove.txt"},
        )
        assert result["deleted"] and result["previous_sha256"] == digest
        assert not target.exists() and not store.unresolved_tool_calls(run_id)
        report.update(
            passed=True,
            private_proposal_kept_original=True,
            authorized_delete_removed_exact_file=True,
            unresolved_calls=0,
        )
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        store.release_lease(run_id, owner)
        (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"Frozen deletion evidence: {root}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--engine",
        type=Path,
        default=Path("apps/desktop/src-tauri/binaries/xaa-engine-x86_64-pc-windows-msvc.exe"),
    )
    asyncio.run(diagnose(parser.parse_args().engine))
