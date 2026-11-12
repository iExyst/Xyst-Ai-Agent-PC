"""Abrupt controller-process exit followed by actual WSL receipt/artifact recovery."""

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from xaa.runtime.command_controller import CommandController
from xaa.runtime.states import RunState
from xaa.runtime.workspace_snapshot import capture_workspace
from xaa.runtime.wsl_transport import WSLTransport
from xaa.storage.runs import RunStore

CHILD = """
import asyncio,json,os,sys,time
from pathlib import Path
from xaa.runtime.command_controller import CommandController
from xaa.runtime.wsl_transport import WSLTransport
from xaa.storage.runs import RunStore
async def main():
    request=json.load(sys.stdin)
    transport=await WSLTransport.discover(request['distribution'],deadline=time.monotonic()+30)
    controller=CommandController(RunStore(Path(request['database'])),transport)
    def crash(*args,**kwargs):
        os._exit(73)
    controller.journal.complete=crash
    await controller.execute(request['run_id'],request['owner'],'command',
        {'argv':['/usr/bin/python3','-I','-c',"import pathlib;print(pathlib.Path('fixture.txt').read_text())"]},
        idempotency_key='command',deadline=time.monotonic()+30)
asyncio.run(main())
"""


async def diagnose(distribution: str, *, profile_drift: bool = False) -> None:
    root = Path(".acceptance") / str(uuid4())
    project = root / "project"
    project.mkdir(parents=True)
    nonce = uuid4().hex
    (project / "fixture.txt").write_text(nonce, encoding="utf-8")
    original = capture_workspace(project).sha256
    transport = await WSLTransport.discover(distribution, deadline=time.monotonic() + 60)
    database = root / "data" / "db.sqlite3"
    cases: list[dict[str, object]] = []
    for damage in (False, True):
        store = RunStore(database)
        record = store.create_run(
            objective="Recover an abruptly exited controller",
            acceptance_criteria=[],
            workspace=str(project),
            mode="workspace",
            model=None,
            command_grant={
                "distribution": distribution,
                "profile_sha256": transport.profile_sha256,
            },
        )
        run_id = record["id"]
        owner = store.acquire_lease(run_id, "crashing-controller", seconds=120)
        if owner is None:
            raise RuntimeError("Cannot acquire diagnostic lease")
        store.transition(run_id, RunState.PLANNING, owner=owner)
        store.transition(run_id, RunState.RUNNING, owner=owner)
        child = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-I", "-c", CHILD],
            input=json.dumps(
                {
                    "database": str(database.resolve()),
                    "run_id": run_id,
                    "owner": owner,
                    "distribution": distribution,
                }
            ).encode(),
            capture_output=True,
            timeout=60,
            creationflags=0x08000000,
        )
        assert child.returncode == 73, "Controller did not reach the intended abrupt exit"
        assert len(store.unresolved_tool_calls(run_id)) == 1
        if damage:
            artifact = next(
                item
                for item in store.list_artifacts(run_id)
                if item["provenance"].startswith("execution.stdout:")
            )
            metadata = store.get_artifact(artifact["id"])
            assert metadata is not None
            (database.parent / "artifacts" / metadata["storage_key"]).unlink()
        # The diagnostic owns the exited child; it releases that dead owner's fixture lease.
        assert store.release_lease(run_id, owner)
        reopened = RunStore(database)
        replacement = reopened.acquire_lease(run_id, "fresh-controller", seconds=120)
        if replacement is None:
            raise RuntimeError("Cannot acquire recovery lease")
        recovery_transport = transport
        if profile_drift:
            # Simulate a newly packaged profile while preserving the live manager
            # invocation; the saved command identity must still be reconciled.
            new_fingerprint = "0" * 64 if transport.profile_sha256 != "0" * 64 else "1" * 64
            recovery_transport = WSLTransport(distribution, new_fingerprint, transport.invocation)
        recovered = await CommandController(reopened, recovery_transport).recover(
            run_id, replacement, "command"
        )
        assert not reopened.unresolved_tool_calls(run_id)
        assert (
            sum(event["event_type"] == "tool.executing" for event in reopened.list_events(run_id))
            == 1
        )
        if damage:
            assert recovered["verification"] == "not_verified" and "exit_code" not in recovered
        else:
            assert recovered["succeeded"] and recovered["result_reconciled"]
            assert nonce in recovered["stdout_excerpt"]
        cases.append(
            {
                "local_stdout_removed": damage,
                "child_exit_code": child.returncode,
                "result": "not_verified" if damage else "recovered_success",
                "launch_events": 1,
                "unresolved_commands": 0,
            }
        )
    assert capture_workspace(project).sha256 == original
    report = {
        "passed": True,
        "distribution": distribution,
        "profile_sha256": transport.profile_sha256,
        "cases": cases,
        "profile_fingerprint_changed_before_recovery": profile_drift,
        "same_distribution_and_execution_identity_retained": True,
        "original_project_unchanged": True,
        "model_or_ui_tool_enabled": False,
        "fault_actor": "automated_diagnostic_not_model",
        "scope": "Controller subprocess crash and saved-identity recovery; not full API/desktop restart or installed package upgrade",
    }
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Command recovery acceptance passed: {root}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", required=True)
    parser.add_argument(
        "--profile-drift",
        action="store_true",
        help="Change the transport fingerprint before reconciling saved WSL identities.",
    )
    args = parser.parse_args()
    asyncio.run(diagnose(args.distribution, profile_drift=args.profile_drift))
