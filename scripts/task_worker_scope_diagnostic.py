"""Exercise task lease fencing through the actual frozen filesystem worker."""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from xaa.runtime.owned_transport import request_process
from xaa.runtime.task_graph import TaskDefinition
from xaa.runtime.tool_worker import WorkerResponse
from xaa.storage.runs import RunStore
from xaa.storage.tasks import TaskStore


async def diagnose(engine: Path) -> None:
    engine = engine.resolve(strict=True)
    root = (Path(".acceptance") / str(uuid4())).resolve()
    project = root / "project"
    project.mkdir(parents=True)
    filename = "task-\u96ea.txt"
    content = "scoped frozen evidence \u96ea"
    (project / filename).write_text(content, encoding="utf-8")
    store = RunStore(root / "data" / "db.sqlite3")
    run_id = store.create_run(
        objective="Frozen task scope fixture",
        acceptance_criteria=[],
        workspace=str(project),
        mode="workspace",
        model=None,
    )["id"]
    parent = store.acquire_lease(run_id, "fixture", seconds=120)
    assert parent is not None
    tasks = TaskStore(store)
    tasks.save_plan(run_id, parent, [TaskDefinition(id="reader", objective="Read the fixture")])
    claim = tasks.claim_ready(run_id, parent)
    assert claim is not None
    request: dict[str, Any] = {
        "database": str(store.path),
        "run_id": run_id,
        "token": parent,
        "call_id": "read",
        "operation": "read",
        "arguments": {"path": filename},
        "idempotency_key": "read",
        "internal": True,
        "task_id": "reader",
        "task_token": claim["token"],
    }
    report: dict[str, Any] = {
        "passed": False,
        "engine_sha256": hashlib.sha256(engine.read_bytes()).hexdigest(),
        "scope": "Actual frozen --tool-worker with System32-only runtime PATH; no model/UI or isolated specialist edit claim",
    }
    saved = {
        key: os.environ.get(key)
        for key in ("PATH", "PYTHONPATH", "VIRTUAL_ENV", "PYTHONIOENCODING")
    }
    try:
        os.environ["PATH"] = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32")
        os.environ.pop("PYTHONPATH", None)
        os.environ.pop("VIRTUAL_ENV", None)
        os.environ["PYTHONIOENCODING"] = "cp1252"

        async def send(payload: dict[str, Any]) -> WorkerResponse:
            raw = await request_process(
                [str(engine), "--tool-worker"],
                json.dumps(payload).encode(),
                timeout=30,
                output_limit=65536,
            )
            return WorkerResponse.model_validate_json(raw)

        actual = await send(request)
        assert actual.ok and actual.result["text"] == content
        denied = await send(
            {
                **request,
                "call_id": "write",
                "operation": "create",
                "arguments": {"path": "forbidden.txt", "content": "never"},
                "idempotency_key": "write",
            }
        )
        assert not denied.ok and denied.error == "PermissionError"
        with store.connection() as connection:
            connection.execute("UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00'")
        expired = await send({**request, "call_id": "expired", "idempotency_key": "expired"})
        assert not expired.ok and expired.error == "PermissionError"
        assert not (project / "forbidden.txt").exists()
        assert (project / filename).read_text(encoding="utf-8") == content
        assert not store.unresolved_tool_calls(run_id)
        assert (
            sum(event["event_type"] == "tool.intended" for event in store.list_events(run_id)) == 1
        )
        store.append_worker_event(run_id, parent, "fixture.parent_lease_retained", {})
        report.update(
            passed=True,
            unicode_read_verified=True,
            parent_write_not_inherited=True,
            expired_task_refused=True,
            parent_lease_retained=True,
            project_unchanged=True,
            intents=1,
            unresolved_calls=0,
        )
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        store.release_lease(run_id, parent)
        (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"Frozen task-scope evidence: {root}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--engine",
        type=Path,
        default=Path("apps/desktop/src-tauri/binaries/xaa-engine-x86_64-pc-windows-msvc.exe"),
    )
    asyncio.run(diagnose(parser.parse_args().engine))
