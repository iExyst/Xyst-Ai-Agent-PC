"""Abrupt controller-process exit followed by actual WSL receipt/artifact recovery."""

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from xaa.runtime.command_controller import CommandController
from xaa.runtime.states import RunState
from xaa.runtime.workspace_snapshot import capture_workspace
from xaa.runtime.wsl_transport import RESOURCE_NAMES, WSLTransport
from xaa.storage.runs import RunStore

CHILD = """
import asyncio,json,os,sys,time
from pathlib import Path
from xaa.runtime.command_controller import CommandController
from xaa.runtime.wsl_transport import WSLTransport
from xaa.storage.runs import RunStore
async def main():
    request=json.load(sys.stdin)
    if request.get('prior_transport_path'):
        import importlib.util
        spec=importlib.util.spec_from_file_location('xaa_prior_wsl_transport',request['prior_transport_path'])
        if spec is None or spec.loader is None: raise RuntimeError('Cannot load prior WSL transport')
        prior=importlib.util.module_from_spec(spec)
        sys.modules[spec.name]=prior
        spec.loader.exec_module(prior)
        WSLTransport=prior.WSLTransport
    else:
        from xaa.runtime.wsl_transport import WSLTransport
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


def stage_prior_profile(root: Path, revision: str) -> tuple[Path, str, str]:
    if not 7 <= len(revision) <= 40 or any(
        character not in "0123456789abcdefABCDEF" for character in revision
    ):
        raise ValueError("Prior profile revision must be a hexadecimal Git commit ID.")
    resolved = subprocess.run(
        ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    prior_directory = root / "prior-profile"
    prior_directory.mkdir()
    for name in (*RESOURCE_NAMES, "wsl_transport.py"):
        content = subprocess.run(
            ["git", "show", f"{resolved}:engine/xaa/runtime/{name}"],
            check=True,
            capture_output=True,
            timeout=10,
        ).stdout
        (prior_directory / name).write_bytes(content)
    fingerprints = {
        name: hashlib.sha256((prior_directory / name).read_bytes()).hexdigest()
        for name in RESOURCE_NAMES
    }
    digest = hashlib.sha256(json.dumps(fingerprints, sort_keys=True).encode()).hexdigest()
    return prior_directory / "wsl_transport.py", digest, resolved


async def diagnose(
    distribution: str,
    *,
    profile_drift: bool = False,
    prior_profile_revision: str | None = None,
) -> None:
    root = Path(".acceptance") / str(uuid4())
    project = root / "project"
    project.mkdir(parents=True)
    nonce = uuid4().hex
    (project / "fixture.txt").write_text(nonce, encoding="utf-8")
    original = capture_workspace(project).sha256
    transport = await WSLTransport.discover(distribution, deadline=time.monotonic() + 60)
    prior_transport_path: Path | None = None
    prior_profile_sha256: str | None = None
    prior_revision: str | None = None
    if prior_profile_revision is not None:
        prior_transport_path, prior_profile_sha256, prior_revision = await asyncio.to_thread(
            stage_prior_profile, root, prior_profile_revision
        )
        if prior_profile_sha256 == transport.profile_sha256:
            raise RuntimeError("Selected Git revision has the same WSL profile fingerprint.")
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
                "profile_sha256": prior_profile_sha256 or transport.profile_sha256,
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
                    **(
                        {"prior_transport_path": str(prior_transport_path.resolve())}
                        if prior_transport_path is not None
                        else {}
                    ),
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
        "profile_fingerprint_changed_before_recovery": profile_drift
        or (prior_profile_sha256 is not None and prior_profile_sha256 != transport.profile_sha256),
        "prior_profile_revision": prior_revision,
        "prior_profile_sha256": prior_profile_sha256,
        "current_profile_sha256": transport.profile_sha256,
        "same_distribution_and_execution_identity_retained": True,
        "original_project_unchanged": True,
        "model_or_ui_tool_enabled": False,
        "fault_actor": "automated_diagnostic_not_model",
        "scope": "Controller subprocess crash and saved-identity recovery; when selected, prior WSL transport and manager source are exercised, not an installed package upgrade",
    }
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Command recovery acceptance passed: {root}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", required=True)
    profile_group = parser.add_mutually_exclusive_group()
    profile_group.add_argument(
        "--profile-drift",
        action="store_true",
        help="Change the transport fingerprint before reconciling saved WSL identities.",
    )
    profile_group.add_argument(
        "--prior-profile-revision",
        help="Run the command with a prior Git revision's WSL transport and manager resources.",
    )
    args = parser.parse_args()
    asyncio.run(
        diagnose(
            args.distribution,
            profile_drift=args.profile_drift,
            prior_profile_revision=args.prior_profile_revision,
        )
    )
