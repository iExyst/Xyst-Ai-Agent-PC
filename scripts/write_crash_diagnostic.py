"""Kill a real worker after fsync and verify destination reconciliation."""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

from xaa.runtime import tool_worker
from xaa.runtime.executor import WorkspaceExecutor
from xaa.runtime.states import RunState
from xaa.storage.runs import RunStore


class InterruptedStore(RunStore):
    def append_worker_event(
        self, run_id: str, owner: str, kind: str, payload: dict[str, Any]
    ) -> None:
        if kind == "tool.executing" and os.environ.get("XAA_CRASH_BOUNDARY") == "created":
            (self.path.parent / "flushed.marker").write_text(
                "file created; content and execution event not committed", encoding="utf-8"
            )
            time.sleep(60)
            raise RuntimeError("Parent should kill this worker before the wait ends.")
        return super().append_worker_event(run_id, owner, kind, payload)

    def record_tool_result(
        self,
        run_id: str,
        token: str,
        call_id: str,
        result: dict[str, Any],
        *,
        internal: bool = False,
    ) -> None:
        (self.path.parent / "flushed.marker").write_text(
            "effect flushed; result not committed", encoding="utf-8"
        )
        time.sleep(60)
        raise RuntimeError("Parent should kill this worker before the wait ends.")


def child(root: Path, run_id: str) -> None:
    store = InterruptedStore(root / "runs.sqlite3")
    token = store.acquire_lease(run_id, "crash-probe", seconds=1)
    if token is None:
        raise RuntimeError("Could not acquire probe lease.")
    store.transition(run_id, RunState.PLANNING, owner=token)
    store.transition(run_id, RunState.RUNNING, owner=token)
    create = os.environ.get("XAA_CRASH_OPERATION") == "create"
    operation = (
        WorkspaceExecutor(store).create_file if create else WorkspaceExecutor(store).write_file
    )
    operation(
        run_id,
        token,
        "edit-1",
        {
            "path": "fixture.txt",
            "content": "verified-after-crash",
            **({} if create else {"expected_sha256": hashlib.sha256(b"before").hexdigest()}),
        },
        idempotency_key="edit-1",
    )


def recover(root: Path, run_id: str) -> None:
    """Recover from a fresh process after the run-owning process was killed."""
    store = RunStore(root / "runs.sqlite3")
    saved = store.get_run(run_id)
    assert saved is not None
    remaining = (
        datetime.fromisoformat(saved["lease_expires_at"]) - datetime.now(UTC)
    ).total_seconds()
    if remaining > 0:
        time.sleep(min(remaining + 0.05, 2))
    recovered = store.recover_abandoned_runs()
    saved = store.get_run(run_id)
    assert saved is not None and saved["state"] == "blocked"
    if os.environ.get("XAA_OWNER_RECONCILIATION") == "1":
        create = os.environ.get("XAA_CRASH_OPERATION") == "create"
        boundary = os.environ.get("XAA_CRASH_BOUNDARY")
        destination = root / "fixture.txt"
        if create:
            if boundary != "created" or not destination.is_file() or destination.read_bytes():
                raise AssertionError("Expected an interrupted create to leave an empty file.")
            # This deterministic diagnostic represents the owner's inspection/removal step.
            destination.unlink()
            inspected_state = "destination_absent"
        else:
            if boundary != "flushed" or not destination.is_file():
                raise AssertionError("Expected an interrupted edit after its content was flushed.")
            if destination.read_bytes() != b"verified-after-crash":
                raise AssertionError("Interrupted edit did not leave the intended content.")
            # Restore the saved pre-write bytes only after inspecting the uncertain edit.
            destination.write_bytes(b"before")
            unresolved = next(
                item for item in store.unresolved_tool_calls(run_id) if item["id"] == "edit-1"
            )
            expected_sha256 = unresolved["arguments"].get("expected_sha256")
            if hashlib.sha256(destination.read_bytes()).hexdigest() != expected_sha256:
                raise AssertionError("Could not restore the saved pre-write file hash.")
            inspected_state = "original_hash_restored"
        token = store.acquire_blocked_run_recovery(
            run_id, "acceptance-owner-reconciliation", seconds=180
        )
        if token is None:
            raise RuntimeError("Could not acquire blocked-run owner recovery.")
        store.transition(run_id, RunState.PLANNING, owner=token)
        frozen = os.environ.get("XAA_FROZEN_WORKER") == "1"
        if not frozen:
            raise ValueError("Owner-reconciliation acceptance requires the frozen worker.")
        executable = Path(
            os.environ.get("XAA_FROZEN_ENGINE_EXECUTABLE", "build/native-dev/xaa-engine.exe")
        ).resolve()
        with ExitStack() as stack:
            stack.enter_context(patch.object(tool_worker.sys, "frozen", True, create=True))
            stack.enter_context(patch.object(tool_worker.sys, "executable", str(executable)))
            result = asyncio.run(
                tool_worker.execute_filesystem(
                    store,
                    run_id,
                    token,
                    "workspace-reconciliation-edit-1",
                    "reconcile_not_applied",
                    {"provider_call_id": "edit-1"},
                    idempotency_key="workspace-reconciliation-edit-1",
                    internal=True,
                    timeout=10,
                )
            )
        store.transition(
            run_id,
            RunState.BLOCKED,
            reason="workspace_write_reconciled_not_applied",
            owner=token,
        )
        store.release_lease(run_id, token)
        resolved = store.unresolved_tool_calls(run_id)
        saved_result = store.public_tool_result(run_id, "edit-1")
        destination_state_valid = (
            not destination.exists()
            if create
            else destination.is_file() and destination.read_bytes() == b"before"
        )
        if (
            result.get("effect") != "not_applied_after_review"
            or resolved
            or saved_result is None
            or json.loads(saved_result).get("effect") != "not_applied_after_review"
            or not destination_state_valid
        ):
            raise AssertionError("Frozen owner reconciliation did not resolve safely.")
        (root / "recovery.json").write_text(
            json.dumps(
                {
                    "automatically_requeued_run_ids": recovered,
                    "state": "blocked",
                    "outcomes": {"edit-1": "not_applied_after_review"},
                    "has_unresolved_tool_calls": False,
                    "original_result_available": True,
                    "owner_inspected_state": inspected_state,
                    "destination_state_valid": destination_state_valid,
                    "run_id": run_id,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return
    store.transition(run_id, RunState.QUEUED, reason="operator_requested_reconciliation")
    token = store.acquire_lease(run_id, "recovery-worker")
    assert token is not None
    store.transition(run_id, RunState.PLANNING, owner=token)
    owned = os.environ.get("XAA_CRASH_RECONCILE_WORKER") == "1"
    frozen = os.environ.get("XAA_FROZEN_WORKER") == "1"
    with ExitStack() as stack:
        if frozen:
            if not owned:
                raise ValueError("Frozen recovery requires worker reconciliation.")
            stack.enter_context(patch.object(tool_worker.sys, "frozen", True, create=True))
            stack.enter_context(
                patch.object(
                    tool_worker.sys,
                    "executable",
                    str(Path("build/native-dev/xaa-engine.exe").resolve()),
                )
            )
        outcomes = (
            asyncio.run(
                tool_worker.execute_filesystem(
                    store,
                    run_id,
                    token,
                    "reconcile",
                    "reconcile",
                    {},
                    idempotency_key="reconcile",
                    internal=True,
                )
            )
            if owned
            else WorkspaceExecutor(store).reconcile_writes(run_id, token)
        )
    if store.unresolved_tool_calls(run_id):
        store.transition(
            run_id, RunState.BLOCKED, reason="tool_reconciliation_required", owner=token
        )
    store.release_lease(run_id, token)
    saved_after = store.get_run(run_id)
    assert saved_after is not None
    result: dict[str, Any] = {
        "automatically_requeued_run_ids": recovered,
        "state": saved_after["state"],
        "outcomes": outcomes,
        "has_unresolved_tool_calls": bool(store.unresolved_tool_calls(run_id)),
    }
    (root / "recovery.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        child(Path(sys.argv[2]), sys.argv[3])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "--recover":
        recover(Path(sys.argv[2]), sys.argv[3])
        return
    root = Path(
        os.environ.get("XAA_ACCEPTANCE_DIR", str(Path(".acceptance") / str(uuid4())))
    ).resolve()
    if root.exists():
        raise FileExistsError("Acceptance output directory already exists.")
    root.mkdir(parents=True)
    target = root / "fixture.txt"
    create = os.environ.get("XAA_CRASH_OPERATION") == "create"
    if not create:
        target.write_bytes(b"before")
    store = RunStore(root / "runs.sqlite3")
    run_id = store.create_run(
        objective="Crash recovery fixture",
        acceptance_criteria=[],
        workspace=str(root),
        mode="workspace",
        model="fixture",
    )["id"]
    with (root / "worker.log").open("w") as log:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--child", str(root), run_id],
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            for _ in range(100):
                if (root / "flushed.marker").exists():
                    break
                if process.poll() is not None:
                    raise RuntimeError("Probe exited before reaching the interruption boundary.")
                time.sleep(0.05)
            else:
                raise TimeoutError("Probe never reached its flushed boundary.")
            process.kill()
            process.wait(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    before_recovery_mtime = target.stat().st_mtime_ns
    recovery_process = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--recover", str(root), run_id],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if recovery_process.returncode != 0:
        raise RuntimeError("Fresh recovery process failed: " + recovery_process.stderr[-1000:])
    recovery = json.loads((root / "recovery.json").read_text(encoding="utf-8"))
    outcomes = recovery["outcomes"]
    early = os.environ.get("XAA_CRASH_BOUNDARY") == "created"
    owner_reconciliation = os.environ.get("XAA_OWNER_RECONCILIATION") == "1"
    target_exists = target.exists()
    target_contents = target.read_bytes() if target_exists else None
    rewritten = target_exists and target.stat().st_mtime_ns != before_recovery_mtime
    expected_owner_state = (
        "destination_absent"
        if os.environ.get("XAA_CRASH_OPERATION") == "create"
        else "original_hash_restored"
    )
    if owner_reconciliation:
        passed = (
            process.returncode not in (0, None)
            and recovery_process.returncode == 0
            and outcomes == {"edit-1": "not_applied_after_review"}
            and recovery["owner_inspected_state"] == expected_owner_state
            and recovery["destination_state_valid"] is True
            and recovery["has_unresolved_tool_calls"] is False
            and recovery["state"] == "blocked"
            and recovery["original_result_available"] is True
            and run_id not in recovery["automatically_requeued_run_ids"]
        )
    else:
        passed = (
            process.returncode not in (0, None)
            and recovery_process.returncode == 0
            and outcomes == {"edit-1": "conflicting" if early else "applied"}
            and target_contents == (b"" if early else b"verified-after-crash")
            and not rewritten
            and recovery["has_unresolved_tool_calls"] == early
            and recovery["state"] == ("blocked" if early else "planning")
            and run_id not in recovery["automatically_requeued_run_ids"]
        )
    report = {
        "passed": passed,
        "operation": "create" if create else "edit",
        "reconciliation_worker": os.environ.get("XAA_CRASH_RECONCILE_WORKER") == "1",
        "frozen_worker": os.environ.get("XAA_FROZEN_WORKER") == "1",
        "run_owner_exit_code": process.returncode,
        "interruption": "run-owning process killed after create before content/execution event"
        if early
        else "run-owning process killed after fsync before result",
        "recovery_in_fresh_process": recovery_process.returncode == 0,
        "automatically_requeued_run_ids": recovery["automatically_requeued_run_ids"],
        "initial_recovery_state": "blocked",
        "state_after_reconciliation": recovery["state"],
        "unresolved_after_reconciliation": recovery["has_unresolved_tool_calls"],
        "outcomes": outcomes,
        "destination_exists": target_exists,
        "owner_inspected_state": recovery.get("owner_inspected_state"),
        "destination_state_valid": recovery.get("destination_state_valid"),
        "original_result_available": recovery.get("original_result_available", False),
        "rewritten": rewritten,
    }
    if os.environ.get("XAA_FROZEN_WORKER") == "1":
        manifest_path = Path(
            os.environ.get("XAA_FROZEN_ENGINE_MANIFEST", "build/native-dev/manifest.json")
        )
        report["build_sha256"] = json.loads(manifest_path.read_text(encoding="utf-8"))
    (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({**report, "evidence": str(root)}, indent=2))
    if not passed:
        raise RuntimeError("Crash-recovery acceptance failed.")


if __name__ == "__main__":
    main()
