"""Actual per-distro admission shared by independent Windows engine databases."""

import argparse
import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from xaa.runtime.command_contracts import CommandIntent
from xaa.runtime.command_controller import CommandController
from xaa.runtime.owned_transport import TransportError, request_process
from xaa.runtime.states import RunState
from xaa.runtime.workspace_snapshot import capture_workspace
from xaa.runtime.wsl_transport import WSLTransport
from xaa.storage.runs import RunStore


@dataclass
class Case:
    store: RunStore
    run_id: str
    owner: str
    controller: CommandController
    intent: CommandIntent | None = None
    task: asyncio.Task[dict[str, Any]] | None = None


async def diagnose(distribution: str) -> None:
    root = (Path(".acceptance") / str(uuid4())).resolve()
    project = root / "project"
    project.mkdir(parents=True)
    nonce = uuid4().hex
    (project / "fixture.txt").write_text(nonce, encoding="utf-8")
    snapshot = capture_workspace(project)
    transport = await WSLTransport.discover(distribution, deadline=time.monotonic() + 30)
    cases: list[Case] = []

    def create() -> Case:
        store = RunStore(root / f"engine-{len(cases) + 1}" / "db.sqlite3")
        run = store.create_run(
            objective="Shared private execution admission",
            acceptance_criteria=[],
            workspace=str(project),
            mode="workspace",
            model=None,
            command_grant={
                "distribution": distribution,
                "profile_sha256": transport.profile_sha256,
            },
        )
        owner = store.acquire_lease(run["id"], "admission-diagnostic", seconds=180)
        if owner is None:
            raise RuntimeError("No diagnostic lease")
        store.transition(run["id"], RunState.PLANNING, owner=owner)
        store.transition(run["id"], RunState.RUNNING, owner=owner)
        case = Case(store, run["id"], owner, CommandController(store, transport))
        cases.append(case)
        return case

    def prepare(case: Case, seconds: int = 30) -> CommandIntent:
        journal = case.controller.journal
        intent = journal.reserve(
            case.run_id,
            case.owner,
            "command",
            {
                "argv": ["/usr/bin/python3", "-I", "-c", "import time;time.sleep(25)"],
                "max_seconds": seconds,
            },
            idempotency_key="command",
        )
        artifact = case.controller.artifact(
            case.run_id, case.owner, intent.execution_id, "snapshot", snapshot.manifest_bytes()
        )
        journal.snapshot_ready(case.run_id, case.owner, "command", snapshot, artifact)
        case.intent = journal.authorize_launch(case.run_id, case.owner, "command")
        return case.intent

    async def status(case: Case) -> dict[str, Any]:
        assert case.intent is not None
        return await transport.control("status", case.intent, deadline=time.monotonic() + 10)

    async def reject(case: Case) -> None:
        intent = prepare(case)
        try:
            await transport.run(intent, snapshot, deadline=time.monotonic() + 40)
        except TransportError:
            pass
        else:
            raise AssertionError("Third private execution bypassed shared admission")
        observed = await status(case)
        assert observed["claim"] is None and observed["admission_reserved"] is False
        await case.controller.reconcile_interrupted(case.run_id, case.owner, "command", intent)

    try:
        first, second = create(), create()
        for case in (first, second):
            intent = prepare(case)
            case.task = asyncio.create_task(
                transport.run(intent, snapshot, deadline=time.monotonic() + 45)
            )
        deadline = time.monotonic() + 10
        while True:
            observations = await asyncio.gather(status(first), status(second))
            if all(item["manager_alive"] and item["admission_reserved"] for item in observations):
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("Two active reservations not observed")
            await asyncio.sleep(0.05)
        await reject(create())
        observed = await status(first)
        claim = observed["claim"]
        kill_code = """
import json,os,pathlib,sys
r=json.load(sys.stdin);pid=r['manager_pid'];assert type(pid) is int and pid>1
assert pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()==r['manager_boot_id']
assert pathlib.Path('/proc',str(pid),'stat').read_text().rsplit(')',1)[1].split()[19]==r['manager_start']
os.kill(pid,9)
"""
        await request_process(
            (*transport.invocation[:6], "/usr/bin/python3", "-I", "-c", kill_code),
            json.dumps(claim).encode(),
            timeout=10,
            output_limit=1024,
        )
        assert first.task is not None
        try:
            await first.task
        except TransportError:
            pass
        else:
            raise AssertionError("Killed manager returned a normal result")
        dead = await status(first)
        assert dead["manager_alive"] is False and dead["admission_reserved"] is True
        assert first.intent is not None
        active = await request_process(
            (
                *transport.invocation[:6],
                "/usr/bin/systemctl",
                "show",
                f"xaa-exec-{first.intent.execution_id}.service",
                "--property=ActiveState",
                "--value",
            ),
            b"",
            timeout=5,
            output_limit=1024,
        )
        assert active.strip() == b"active"
        await reject(create())
        first.store.release_lease(first.run_id, first.owner)
        replacement = first.store.acquire_lease(
            first.run_id, "recovery-after-manager-death", seconds=120
        )
        if replacement is None:
            raise RuntimeError("No recovery lease")
        recovered = await first.controller.recover(first.run_id, replacement, "command")
        assert recovered["verification"] == "not_verified" and "exit_code" not in recovered
        assert (await status(first))["admission_reserved"] is False
        assert (await status(second))["admission_reserved"] is True
        fifth = create()
        result = await fifth.controller.execute(
            fifth.run_id,
            fifth.owner,
            "command",
            {
                "argv": [
                    "/usr/bin/python3",
                    "-I",
                    "-c",
                    "import pathlib;print(pathlib.Path('fixture.txt').read_text())",
                ]
            },
            idempotency_key="command",
            deadline=time.monotonic() + 20,
        )
        assert result["succeeded"] and nonce in result["stdout_excerpt"]
        assert second.intent is not None
        await second.controller.reconcile_interrupted(
            second.run_id, second.owner, "command", second.intent
        )
        assert second.task is not None
        await second.task
        assert (await status(second))["admission_reserved"] is False
        assert all(not case.store.unresolved_tool_calls(case.run_id) for case in cases)
        assert capture_workspace(project).sha256 == snapshot.sha256
    finally:
        for case in cases:
            if case.intent is None:
                with case.store.connection() as connection:
                    row = connection.execute(
                        "SELECT arguments FROM tool_calls WHERE run_id=? AND provider_call_id='command'",
                        (case.run_id,),
                    ).fetchone()
                if row is not None:
                    case.intent = CommandIntent.model_validate_json(row[0])
            if case.intent is not None:
                try:
                    await transport.control("cleanup", case.intent, deadline=time.monotonic() + 10)
                except (OSError, RuntimeError, ValueError):
                    pass
        tasks = [case.task for case in cases if case.task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    report = {
        "passed": True,
        "distribution": distribution,
        "profile_sha256": transport.profile_sha256,
        "independent_windows_databases": len(cases),
        "two_concurrent_reservations_observed": True,
        "third_execution_refused_before_claim": True,
        "manager_sigkill_retained_slot_and_active_unit": True,
        "capacity_still_refused_after_manager_death": True,
        "fresh_cleanup_released_slot": True,
        "new_execution_succeeded_after_cleanup": True,
        "unresolved_commands": 0,
        "original_project_unchanged": True,
        "fault_actor": "automated_diagnostic_not_model",
        "scope": "Current manager profile within one selected distro, not cross-distro host admission",
    }
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Shared command admission acceptance passed: {root}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", required=True)
    asyncio.run(diagnose(parser.parse_args().distribution))
