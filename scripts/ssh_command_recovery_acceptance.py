"""Verify SSH command reconciliation after an engine process exits at the commit boundary."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, NoReturn

from xaa.runtime.command_contracts import CommandGrant
from xaa.runtime.command_controller import CommandController
from xaa.runtime.command_transport_factory import discover_command_transport
from xaa.runtime.ssh_transport import SSH_BACKEND, SSHTransport
from xaa.runtime.states import RunState
from xaa.storage.commands import CommandJournal
from xaa.storage.runs import RunStore

CALL_ID = "ssh-recovery-acceptance"
EXIT_AFTER_REMOTE_COMMIT = 73


def _crash_before_local_completion(*_args: Any, **_kwargs: Any) -> NoReturn:
    """Exit only after remote output and its local artifacts have been persisted."""
    raise SystemExit(EXIT_AFTER_REMOTE_COMMIT)


async def _worker(args: argparse.Namespace) -> None:
    store = RunStore(args.database)
    transport = await SSHTransport.discover(
        args.alias, args.identity, deadline=time.monotonic() + 20
    )
    controller = CommandController(store, transport)
    controller.journal.complete = _crash_before_local_completion  # type: ignore[method-assign]
    await controller.execute(
        args.run_id,
        args.owner_token,
        CALL_ID,
        {"argv": ["/usr/bin/python3", "-I", "-c", "print('ssh-recovery-verified')"]},
        idempotency_key=CALL_ID,
        deadline=time.monotonic() + 30,
    )
    raise RuntimeError("The simulated engine exit did not occur.")


async def _accept(args: argparse.Namespace) -> dict[str, object]:
    if sys.platform != "win32":
        raise RuntimeError("This acceptance must run on Windows with Windows OpenSSH installed.")
    identity = Path(args.identity).resolve(strict=True)
    transport = await SSHTransport.discover(
        args.alias, str(identity), deadline=time.monotonic() + 20
    )
    with tempfile.TemporaryDirectory(prefix="xaa-ssh-recovery-") as temporary:
        root = Path(temporary)
        workspace = root / "project"
        workspace.mkdir()
        (workspace / "fixture.txt").write_text("recovery fixture\n", encoding="utf-8")
        database = root / "app-data" / "xaa.sqlite3"
        store = RunStore(database)
        grant = CommandGrant(
            backend=SSH_BACKEND,
            distribution=args.alias,
            profile_sha256=transport.profile_sha256,
            ssh_identity_file=str(identity),
        )
        run = store.create_run(
            objective="Recover a finished remote command after an engine restart",
            acceptance_criteria=[],
            workspace=str(workspace),
            mode="workspace",
            model=None,
            command_grant=grant.model_dump(mode="json"),
        )
        run_id = str(run["id"])
        owner = store.acquire_lease(run_id, "ssh-recovery-worker", seconds=60)
        if owner is None:
            raise RuntimeError("Could not acquire the acceptance run lease.")
        store.transition(run_id, RunState.PLANNING, owner=owner)
        store.transition(run_id, RunState.RUNNING, owner=owner)

        worker = await asyncio.create_subprocess_exec(
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            "--database",
            str(database),
            "--run-id",
            run_id,
            "--owner-token",
            owner,
            "--alias",
            args.alias,
            "--identity",
            str(identity),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            worker_stdout, worker_stderr = await asyncio.wait_for(worker.communicate(), timeout=60)
        except TimeoutError:
            worker.kill()
            await worker.wait()
            raise
        if worker.returncode != EXIT_AFTER_REMOTE_COMMIT:
            raise RuntimeError(
                "The engine worker did not exit at the simulated commit boundary: "
                f"exit={worker.returncode}, stdout={worker_stdout[-2000:]!r}, "
                f"stderr={worker_stderr[-2000:]!r}"
            )
        unresolved_before = store.unresolved_tool_calls(run_id)
        artifacts_before = store.list_artifacts(run_id)
        if len(unresolved_before) != 1 or not any(
            item["provenance"].startswith("execution.stdout:") for item in artifacts_before
        ):
            raise RuntimeError("The interrupted command lacks its durable recovery record.")

        if not store.release_lease(run_id, owner):
            raise RuntimeError("The exited worker lease could not be released for recovery.")
        recovered_store = RunStore(database)
        recovery_owner = recovered_store.acquire_lease(run_id, "ssh-recovery-after-restart")
        if recovery_owner is None:
            raise RuntimeError("Could not acquire a replacement recovery lease.")
        intent = CommandJournal(recovered_store).recovery_intent(run_id, recovery_owner, CALL_ID)
        fresh_transport = await discover_command_transport(intent, deadline=time.monotonic() + 20)
        result = await CommandController(recovered_store, fresh_transport).recover(
            run_id, recovery_owner, CALL_ID
        )
        if (
            result.get("succeeded") is not True
            or result.get("result_reconciled") is not True
            or recovered_store.unresolved_tool_calls(run_id)
        ):
            raise RuntimeError("The restarted engine did not reconcile the saved SSH result.")
        stdout = [
            item
            for item in recovered_store.list_artifacts(run_id)
            if item["provenance"].startswith("execution.stdout:")
        ]
        if len(stdout) != 1:
            raise RuntimeError("Recovery did not preserve exactly one matching stdout artifact.")
        metadata = recovered_store.get_artifact(stdout[0]["id"])
        if metadata is None or not isinstance(metadata.get("storage_key"), str):
            raise RuntimeError("The recovered stdout artifact metadata is missing.")
        output_path = recovered_store.path.parent / "artifacts" / metadata["storage_key"]
        if output_path.read_text(encoding="utf-8") != "ssh-recovery-verified\n":
            raise RuntimeError("Recovered output does not match the remote command result.")
        return {
            "backend": SSH_BACKEND,
            "distribution": args.alias,
            "profile_sha256": transport.profile_sha256,
            "remote_manager_sha256": transport.manager_profile_sha256,
            "worker_exit_code": worker.returncode,
            "unresolved_before_recovery": len(unresolved_before),
            "durable_output_artifacts_before_recovery": len(artifacts_before),
            "recovered": result,
            "recovery_artifact_sha256": metadata["sha256"],
            "recovery_artifact_bytes": metadata["size_bytes"],
            "unresolved_after_recovery": 0,
            "model_inference_requested": False,
        }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alias", required=True, help="Saved OpenSSH alias in the current profile")
    parser.add_argument("--identity", required=True, help="Private key under the current .ssh")
    parser.add_argument("--report", type=Path, help="Optional path for the JSON acceptance report")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--database", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--run-id", help=argparse.SUPPRESS)
    parser.add_argument("--owner-token", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        if not all((args.database, args.run_id, args.owner_token)):
            raise ValueError("The internal worker invocation is incomplete.")
        await _worker(args)
        return
    result = await _accept(args)
    encoded = json.dumps(result, indent=2, sort_keys=True)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    asyncio.run(main())
