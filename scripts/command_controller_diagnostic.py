"""Actual grant-to-snapshot-to-WSL-to-artifact command-controller acceptance."""

import argparse
import asyncio
import json
import time
from pathlib import Path
from uuid import uuid4

from xaa.runtime.command_contracts import CommandIntent
from xaa.runtime.command_controller import CommandController
from xaa.runtime.states import RunState
from xaa.runtime.workspace_snapshot import capture_workspace
from xaa.runtime.wsl_transport import WSLTransport
from xaa.storage.runs import RunStore


async def diagnose(distribution: str) -> None:
    root = Path(".acceptance") / str(uuid4())
    project = root / "project"
    project.mkdir(parents=True)
    nonce = uuid4().hex
    (project / "fixture.txt").write_text(nonce, encoding="utf-8")
    snapshot = capture_workspace(project)
    transport = await WSLTransport.discover(distribution, deadline=time.monotonic() + 60)
    store = RunStore(root / "data" / "db.sqlite3")
    controller = CommandController(store, transport)

    def run():
        record = store.create_run(
            objective="Verify actual private command controller",
            acceptance_criteria=[],
            workspace=str(project),
            mode="workspace",
            model=None,
            command_grant={
                "distribution": distribution,
                "profile_sha256": transport.profile_sha256,
            },
        )
        token = store.acquire_lease(record["id"], "controller-diagnostic", seconds=120)
        if token is None:
            raise RuntimeError("Cannot acquire diagnostic lease")
        store.transition(record["id"], RunState.PLANNING, owner=token)
        store.transition(record["id"], RunState.RUNNING, owner=token)
        return record["id"], token

    normal, owner = run()
    result = await controller.execute(
        normal,
        owner,
        "normal",
        {
            "argv": [
                "/usr/bin/python3",
                "-I",
                "-c",
                "import pathlib;print(pathlib.Path('fixture.txt').read_text());pathlib.Path('private.txt').write_text('private')",
            ]
        },
        idempotency_key="normal",
        deadline=time.monotonic() + 30,
    )
    assert result["succeeded"] and nonce in result["stdout_excerpt"]
    assert not store.unresolved_tool_calls(normal) and len(store.list_artifacts(normal)) == 3
    assert capture_workspace(project).sha256 == snapshot.sha256

    interrupted, owner = run()
    task = asyncio.create_task(
        controller.execute(
            interrupted,
            owner,
            "cancel",
            {
                "argv": ["/usr/bin/python3", "-I", "-c", "import time;time.sleep(25)"],
                "max_seconds": 30,
            },
            idempotency_key="cancel",
            deadline=time.monotonic() + 50,
        )
    )
    try:
        deadline = time.monotonic() + 20
        while True:
            pending = store.unresolved_tool_calls(interrupted)
            if pending:
                identity = CommandIntent.model_validate(pending[0]["arguments"])
                status = await transport.control("status", identity, deadline=deadline)
                if status["manager_alive"]:
                    break
            if task.done():
                await task
                raise AssertionError("Workload ended before cancellation")
            if time.monotonic() >= deadline:
                raise TimeoutError("No active manager observed")
            await asyncio.sleep(0.05)
        stopped = store.transition(interrupted, RunState.PAUSED)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert not store.unresolved_tool_calls(interrupted)
    saved = store.get_run(interrupted)
    assert (
        saved is not None
        and saved["state"] == "paused"
        and saved["revision"] == stopped["revision"]
    )
    assert any(
        event["event_type"] == "execution.reconciled" for event in store.list_events(interrupted)
    )
    with store.connection() as connection:
        interrupted_call = connection.execute(
            "SELECT state,result FROM tool_calls WHERE run_id=? AND provider_call_id='cancel'",
            (interrupted,),
        ).fetchone()
    recorded = json.loads(interrupted_call["result"])
    assert interrupted_call["state"] == "failed" and recorded["verification"] == "not_verified"
    assert recorded["ok"] is False and "exit_code" not in recorded
    assert capture_workspace(project).sha256 == snapshot.sha256
    report = {
        "passed": True,
        "distribution": distribution,
        "profile_sha256": transport.profile_sha256,
        "owned_snapshot_to_registered_result": True,
        "normal_artifact_count": 3,
        "run_pause_revoked_lease_before_cancellation": True,
        "stopped_revision_preserved": True,
        "interrupted_command_reconciled_without_success_or_replay": True,
        "unresolved_commands": 0,
        "original_project_unchanged": True,
        "fault_actor": "automated_diagnostic_not_model",
        "model_or_ui_tool_enabled": False,
    }
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Command controller acceptance passed: {root}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", required=True)
    asyncio.run(diagnose(parser.parse_args().distribution))
