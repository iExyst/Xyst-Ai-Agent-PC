"""Real supervisor startup discovers and cleans an expired controller reservation."""

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from command_recovery_diagnostic import CHILD
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.command_controller import CommandController
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.states import RunState
from xaa.runtime.supervisor import RunSupervisor
from xaa.runtime.workspace_snapshot import capture_workspace
from xaa.runtime.wsl_transport import WSLTransport
from xaa.storage.commands import CommandJournal
from xaa.storage.runs import RunStore

ORPHAN_CHILD = """
import asyncio,json,os,sys,time
from pathlib import Path
from xaa.runtime.command_controller import CommandController
from xaa.runtime.wsl_transport import WSLTransport
from xaa.storage.runs import RunStore
async def main():
    request=json.load(sys.stdin)
    transport=await WSLTransport.discover(request['distribution'],deadline=time.monotonic()+30)
    controller=CommandController(RunStore(Path(request['database'])),transport)
    # Observe the real unit from a concurrent task before abrupt controller death.
    task=asyncio.create_task(controller.execute(request['run_id'],request['owner'],'command',
        {'argv':['/usr/bin/python3','-I','-c','import time;time.sleep(25)'],'max_seconds':20},
        idempotency_key='command',deadline=time.monotonic()+30))
    from xaa.runtime.command_contracts import CommandIntent
    while not task.done():
        pending=controller.store.unresolved_tool_calls(request['run_id'])
        if pending:
            intent=CommandIntent.model_validate(pending[0]['arguments'])
            status=await transport.control('status',intent,deadline=time.monotonic()+5)
            check=await asyncio.create_subprocess_exec(*transport.invocation[:6],
                '/usr/bin/systemctl','is-active','xaa-exec-'+intent.execution_id+'.service',
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,
                creationflags=0x08000000)
            output,_=await check.communicate()
            if status['manager_alive'] and check.returncode == 0 and output.strip() == b'active':
                os._exit(74)
        await asyncio.sleep(0.05)
    await task
    raise RuntimeError('No active execution observed')
asyncio.run(main())
"""


async def diagnose(distribution: str, *, orphan_active: bool = False) -> None:
    root = Path(".acceptance") / str(uuid4())
    project = root / "project"
    project.mkdir(parents=True)
    nonce = uuid4().hex
    (project / "fixture.txt").write_text(nonce, encoding="utf-8")
    original = capture_workspace(project).sha256
    store = RunStore(root / "data" / "db.sqlite3")
    transport = await WSLTransport.discover(distribution, deadline=time.monotonic() + 60)
    record = store.create_run(
        objective="Recover after controller exit",
        acceptance_criteria=[],
        workspace=str(project),
        mode="workspace",
        model=None,
        command_grant={"distribution": distribution, "profile_sha256": transport.profile_sha256},
    )
    run_id = record["id"]
    owner = store.acquire_lease(run_id, "exiting-controller", seconds=120)
    if owner is None:
        raise RuntimeError("No diagnostic lease")
    store.transition(run_id, RunState.PLANNING, owner=owner)
    store.transition(run_id, RunState.RUNNING, owner=owner)
    child = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-I", "-c", ORPHAN_CHILD if orphan_active else CHILD],
        input=json.dumps(
            {
                "database": str(store.path.resolve()),
                "run_id": run_id,
                "owner": owner,
                "distribution": distribution,
            }
        ).encode(),
        capture_output=True,
        timeout=60,
        creationflags=0x08000000,
    )
    assert child.returncode == (74 if orphan_active else 73)
    with store.connection() as connection:
        connection.execute(
            "UPDATE runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (run_id,)
        )
    provider = LMStudioProvider()
    supervisor = RunSupervisor(Coordinator(store, provider))
    supervisor.start()
    try:
        async with asyncio.timeout(30):
            while not any(
                event["event_type"] in {"execution.cleanup_observed", "execution.reconciled"}
                for event in store.list_events(run_id)
            ):
                assert supervisor.recovery_task is not None
                if supervisor.recovery_task.done():
                    await supervisor.recovery_task
                    raise AssertionError("Startup recovery ended before observation")
                await asyncio.sleep(0.05)
        stopped = store.get_run(run_id)
        assert (
            stopped is not None and stopped["state"] == "blocked" and stopped["lease_owner"] is None
        )
        if not orphan_active:
            assert len(store.unresolved_tool_calls(run_id)) == 1
        assert CommandJournal(store).stopped_candidates() == []
        assert not supervisor.tasks
    finally:
        await supervisor.close()
        await provider.close()
    # A later deliberate recovery can still verify the preserved local result.
    if store.unresolved_tool_calls(run_id):
        store.transition(run_id, RunState.QUEUED)
        replacement = store.acquire_lease(run_id, "explicit-review", seconds=120)
        if replacement is None:
            raise RuntimeError("No review lease")
        result = await CommandController(RunStore(store.path), transport).recover(
            run_id, replacement, "command"
        )
    else:
        with store.connection() as connection:
            row = connection.execute(
                "SELECT result FROM tool_calls WHERE run_id=?", (run_id,)
            ).fetchone()
        result = json.loads(row[0])
    if orphan_active:
        assert result["verification"] == "not_verified" and "exit_code" not in result
    else:
        assert (
            result["succeeded"]
            and result["result_reconciled"]
            and nonce in result["stdout_excerpt"]
        )
    assert not store.unresolved_tool_calls(run_id)
    launch_count = sum(
        event["event_type"] == "tool.executing" for event in store.list_events(run_id)
    )
    assert launch_count == 1
    assert capture_workspace(project).sha256 == original
    report = {
        "passed": True,
        "distribution": distribution,
        "profile_sha256": transport.profile_sha256,
        "child_exit_code": child.returncode,
        "active_manager_and_unit_observed_before_crash": orphan_active,
        "fixture_lease_expiry_injected_after_observed_exit": True,
        "supervisor_discovered_and_cleaned": True,
        "run_remained_blocked_for_review": True,
        "available_result_preserved_then_verified": not orphan_active,
        "unresolved_after_explicit_review": 0,
        "coordinator_tasks_dispatched": 0,
        "launch_events": launch_count,
        "original_project_unchanged": True,
        "model_or_ui_tool_enabled": False,
        "fault_actor": "automated_diagnostic_not_model",
    }
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Command startup recovery acceptance passed: {root}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", required=True)
    parser.add_argument("--orphan-active", action="store_true")
    args = parser.parse_args()
    asyncio.run(diagnose(args.distribution, orphan_active=args.orphan_active))
