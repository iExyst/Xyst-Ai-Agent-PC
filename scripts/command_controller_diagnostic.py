"""Actual grant-to-snapshot-to-WSL-to-artifact command-controller acceptance."""

import argparse
import asyncio
import json
import subprocess
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

    outside = root / "outside"
    outside.mkdir()
    sentinel = outside / "private.txt"
    sentinel.write_bytes(b"external command fixture")

    async def reject_reparse_point(call_id: str) -> None:
        rejected, owner = run()
        try:
            await controller.execute(
                rejected,
                owner,
                call_id,
                {"argv": ["/usr/bin/true"]},
                idempotency_key=call_id,
                deadline=time.monotonic() + 30,
            )
        except RuntimeError as exc:
            if "Owned transport failed" not in str(exc):
                raise
        else:
            raise AssertionError("Workspace reparse point was accepted for command capture")
        events = store.list_events(rejected)
        event_types = [event["event_type"] for event in events]
        assert "execution.reserved" in event_types
        assert "execution.launched" not in event_types
        assert "execution.reconciled" in event_types
        assert not store.unresolved_tool_calls(rejected)
        assert sentinel.read_bytes() == b"external command fixture"

    junction = project / "external-junction"
    await asyncio.to_thread(
        subprocess.run,
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        check=True,
        capture_output=True,
    )
    try:
        await reject_reparse_point("junction")
    finally:
        junction.rmdir()

    reparse_types = ["directory_junction"]
    unavailable_reparse_types: dict[str, str] = {}
    symlink = project / "external-symlink"
    try:
        symlink.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        if getattr(exc, "winerror", None) != 1314:
            raise
        unavailable_reparse_types["directory_symlink"] = "winerror_1314_privilege_unavailable"
    else:
        try:
            await reject_reparse_point("directory-symlink")
            reparse_types.append("directory_symlink")
        finally:
            symlink.unlink()

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
    cleanup_entered = asyncio.Event()
    release_cleanup = asyncio.Event()
    original_reconcile = controller.reconcile_interrupted

    async def controlled_reconcile(run_id: str, owner: str, call_id: str, intent: CommandIntent):
        cleanup_entered.set()
        await release_cleanup.wait()
        return await original_reconcile(run_id, owner, call_id, intent)

    controller.reconcile_interrupted = controlled_reconcile
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
        await asyncio.wait_for(cleanup_entered.wait(), timeout=10)
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        release_cleanup.set()
        try:
            await task
        except asyncio.CancelledError:
            pass
    finally:
        release_cleanup.set()
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
        "windows_reparse_types_rejected_before_wsl_launch": reparse_types,
        "windows_reparse_types_unavailable": unavailable_reparse_types,
        "owned_snapshot_to_registered_result": True,
        "normal_artifact_count": 3,
        "run_pause_revoked_lease_before_cancellation": True,
        "stopped_revision_preserved": True,
        "repeated_cancellation_waited_for_cleanup": True,
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
