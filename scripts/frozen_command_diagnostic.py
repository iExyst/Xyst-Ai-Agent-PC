"""Frozen snapshot IPC and authenticated API startup using bundled WSL resources."""

import argparse
import asyncio
import base64
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
from build_engine import verify_execution_resources
from command_recovery_diagnostic import CHILD
from command_startup_diagnostic import ORPHAN_CHILD
from xaa.runtime.command_controller import CommandController
from xaa.runtime.owned_transport import request_process
from xaa.runtime.processes import stop_owned_process
from xaa.runtime.snapshot_worker import SnapshotRequest, add_selected_skill_scripts
from xaa.runtime.states import RunState
from xaa.runtime.workspace_snapshot import MAX_WIRE_BYTES, WorkspaceSnapshot, capture_workspace
from xaa.runtime.wsl_transport import WSLTransport, profile_fingerprint
from xaa.storage.commands import CommandJournal
from xaa.storage.runs import RunStore
from xaa.storage.skills import SkillAsset, SkillDraft, SkillStore


async def diagnose(
    engine: Path, distribution: str, orphan_active: bool, *, snapshot_only: bool = False
) -> None:
    engine = engine.resolve(strict=True)
    resources = verify_execution_resources(engine)
    root = (Path(".acceptance") / str(uuid4())).resolve()
    project = root / "project"
    project.mkdir(parents=True)
    nonce = uuid4().hex
    (project / "fixture.txt").write_text(nonce, encoding="utf-8")
    (project / "\u96ea.dat").write_bytes(b"\x00\xff" * (1024 * 1024))
    (project / "xaa").mkdir()
    (project / "xaa" / "__init__.py").write_text(
        "raise RuntimeError('project imports forbidden')", encoding="utf-8"
    )
    data_root = root / "app-data"
    store = RunStore(data_root / "XAA" / "xaa.sqlite3")
    script = b"print('frozen selected skill')\n"
    skill = SkillStore(store).create(
        SkillDraft(
            name="Frozen selected script fixture",
            content="Run the selected script only in the granted sandbox.",
            required_tools=[],
            required_permissions=["workspace.read"],
            assets=[
                SkillAsset(
                    path="scripts/check.py",
                    content_base64=base64.b64encode(script).decode("ascii"),
                    media_type="text/x-python",
                    sha256=hashlib.sha256(script).hexdigest(),
                )
            ],
        )
    )
    transport = (
        None
        if snapshot_only
        else await WSLTransport.discover(distribution, deadline=time.monotonic() + 60)
    )
    record = store.create_run(
        objective="Frozen private execution recovery",
        acceptance_criteria=[],
        workspace=str(project),
        mode="workspace",
        model=None,
        command_grant={"distribution": distribution, "profile_sha256": profile_fingerprint()[0]},
        procedure_trial={"skill_id": skill["id"], "version": 1},
    )
    run_id = record["id"]
    original_snapshot = capture_workspace(project)
    snapshot = add_selected_skill_scripts(original_snapshot, store.procedure_trial(run_id))
    owner = store.acquire_lease(run_id, "frozen-diagnostic", seconds=180)
    if owner is None:
        raise RuntimeError("No fixture lease")
    store.transition(run_id, RunState.PLANNING, owner=owner)
    store.transition(run_id, RunState.RUNNING, owner=owner)
    intent = CommandJournal(store).reserve(
        run_id, owner, "snapshot", {"argv": ["/usr/bin/true"]}, idempotency_key="snapshot"
    )
    temporary = root / "temporary \u96ea $% folder"
    temporary.mkdir()
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        in {
            "SYSTEMROOT",
            "WINDIR",
            "LOCALAPPDATA",
            "APPDATA",
            "USERPROFILE",
            "USERNAME",
            "USERDOMAIN",
        }
    }
    environment.update(
        PATH=str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"),
        TEMP=str(temporary),
        TMP=str(temporary),
    )
    request = SnapshotRequest(
        database=str(store.path), run_id=run_id, owner=owner, call_id="snapshot"
    )
    with patch.dict(os.environ, environment, clear=True):
        raw = await request_process(
            [str(engine), "--snapshot-worker"],
            request.model_dump_json().encode(),
            timeout=30,
            output_limit=MAX_WIRE_BYTES,
        )
    frozen_snapshot = WorkspaceSnapshot.from_bytes(raw)
    assert frozen_snapshot.sha256 == snapshot.sha256
    selected_script_staged = any(
        entry.path.endswith("/scripts/check.py") and entry.content == script
        for entry in frozen_snapshot.files
    )
    assert selected_script_staged
    assert capture_workspace(project).sha256 == original_snapshot.sha256
    assert not any(event["event_type"] == "tool.executing" for event in store.list_events(run_id))
    if snapshot_only:
        store.transition(run_id, RunState.CANCELLED)
        report = {
            "passed": True,
            "engine_sha256": hashlib.sha256(engine.read_bytes()).hexdigest(),
            "resource_sha256": resources,
            "profile_sha256": profile_fingerprint()[0],
            "frozen_snapshot_matches": True,
            "selected_skill_script_staged": selected_script_staged,
            "binary_bytes": 2 * 1024 * 1024,
            "path_system32_only": True,
            "project_import_shadow_ignored": True,
            "no_execution_dispatched": True,
            "original_project_unchanged": True,
            "extraction_path_spaces_unicode_dollar_percent": True,
            "scope": "Frozen snapshot IPC only; no WSL launch",
            "fixture_reservation": "cancelled run, undispatched intention retained in diagnostic-only database",
        }
        (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"Frozen snapshot smoke passed: {root}")
        return
    assert transport is not None
    await CommandController(store, transport).reconcile_interrupted(
        run_id, owner, "snapshot", intent
    )
    child = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-I", "-c", ORPHAN_CHILD if orphan_active else CHILD],
        input=json.dumps(
            {
                "database": str(store.path),
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
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    token = secrets.token_hex(32)
    environment.update(XAA_DATA_DIR=str(data_root), XAA_API_TOKEN=token, XAA_API_PORT=str(port))
    base = f"http://127.0.0.1:{port}"
    process = None
    with (root / "engine.log").open("wb") as log:
        process = await asyncio.create_subprocess_exec(
            str(engine),
            cwd=project,
            env=environment,
            stdout=log,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=0x08000000,
        )
        try:
            async with httpx.AsyncClient(base_url=base, timeout=5) as client:
                deadline = time.monotonic() + 40
                while True:
                    if process.returncode is not None:
                        raise RuntimeError("Frozen API exited before readiness")
                    try:
                        response = await client.get(
                            f"/runs/{run_id}", headers={"Authorization": "Bearer " + token}
                        )
                        if response.status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Frozen API startup timed out")
                    await asyncio.sleep(0.1)
                assert (await client.get(f"/runs/{run_id}")).status_code == 401
                while True:
                    response = await client.get(
                        f"/runs/{run_id}/events", headers={"Authorization": "Bearer " + token}
                    )
                    response.raise_for_status()
                    if any(
                        event["event_type"]
                        in {"execution.cleanup_observed", "execution.reconciled"}
                        and event["payload"].get("call_id") == "command"
                        for event in response.json()
                    ):
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Frozen recovery did not confirm cleanup")
                    await asyncio.sleep(0.1)
                saved = store.get_run(run_id)
                assert (
                    saved is not None
                    and saved["state"] == "blocked"
                    and saved["lease_owner"] is None
                )
                stopped = await client.post(
                    "/shutdown", headers={"Authorization": "Bearer " + token}
                )
                stopped.raise_for_status()
                await asyncio.wait_for(process.wait(), 15)
                assert process.returncode == 0
        finally:
            await stop_owned_process(process)
    if store.unresolved_tool_calls(run_id):
        store.transition(run_id, RunState.QUEUED)
        replacement = store.acquire_lease(run_id, "explicit-review", seconds=120)
        if replacement is None:
            raise RuntimeError("No review lease")
        result = await CommandController(store, transport).recover(run_id, replacement, "command")
        if not orphan_active:
            assert result["succeeded"] and nonce in result["stdout_excerpt"]
        else:
            assert result["verification"] == "not_verified"
    assert not store.unresolved_tool_calls(run_id)
    assert capture_workspace(project).sha256 == original_snapshot.sha256
    report = {
        "passed": True,
        "engine_sha256": hashlib.sha256(engine.read_bytes()).hexdigest(),
        "resource_sha256": resources,
        "profile_sha256": profile_fingerprint()[0],
        "frozen_snapshot_matches": True,
        "selected_skill_script_staged": selected_script_staged,
        "binary_bytes": 2 * 1024 * 1024,
        "project_import_shadow_ignored": True,
        "path_system32_only": True,
        "frozen_api_authenticated": True,
        "unauthenticated_status": 401,
        "frozen_startup_cleanup_observed": True,
        "active_orphan_case": orphan_active,
        "run_kept_blocked": True,
        "graceful_exit_code": process.returncode,
        "original_project_unchanged": True,
        "extraction_path_spaces_unicode_dollar_percent": True,
        "unresolved_after_explicit_source_review": 0,
        "scope": "Frozen sidecar, not installed GUI; injected expiry after observed controller exit",
        "ui_or_model_command_tool_enabled": False,
    }
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Frozen command acceptance passed: {root}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--engine",
        type=Path,
        default=Path("apps/desktop/src-tauri/binaries/xaa-engine-x86_64-pc-windows-msvc.exe"),
    )
    parser.add_argument("--distribution")
    parser.add_argument("--orphan-active", action="store_true")
    parser.add_argument("--snapshot-only", action="store_true")
    args = parser.parse_args()
    if not args.snapshot_only and not args.distribution:
        parser.error("--distribution is required for WSL recovery acceptance")
    asyncio.run(
        diagnose(
            args.engine,
            args.distribution or "snapshot-smoke",
            args.orphan_active,
            snapshot_only=args.snapshot_only,
        )
    )
