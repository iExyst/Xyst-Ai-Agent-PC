import asyncio
import base64
import hashlib
import os
import subprocess
import time

import pytest
from xaa.runtime.command_controller import CommandController
from xaa.runtime.states import RunState
from xaa.storage.commands import CommandJournal
from xaa.storage.runs import LeaseLost, RunStore


class FixtureTransport:
    def __init__(self, *, corrupt=False):
        self.corrupt = corrupt
        self.controls = []

    def require_intent(self, intent):
        assert intent.profile_sha256 == "a" * 64

    async def run(self, intent, snapshot, *, deadline):
        out = b"untrusted command bytes"
        return {
            "execution_id": intent.execution_id,
            "snapshot_sha256": snapshot.sha256,
            "filter_sha256": "f" * 64,
            "termination_reason": "finished",
            "exit_code": 0,
            "supervisor_exit_code": 0,
            "succeeded": True,
            "output_complete": True,
            "cleanup_confirmed": True,
            "elapsed_seconds": 0.1,
            "stdout_b64": base64.b64encode(out).decode(),
            "stderr_b64": "",
            "stdout_bytes": len(out),
            "stderr_bytes": 0,
            "stdout_sha256": "0" * 64 if self.corrupt else hashlib.sha256(out).hexdigest(),
            "stderr_sha256": hashlib.sha256(b"").hexdigest(),
        }

    async def control(self, action, intent, *, deadline):
        self.controls.append(action)
        return {
            "execution_id": intent.execution_id,
            "cleanup_confirmed": True,
            "closed": True,
            "manager_alive": False,
            "outcome": None,
        }


def setup(root, transport, *, procedure_trial=None):
    project = root / "project"
    project.mkdir()
    (project / "fixture.txt").write_text("fixture", encoding="utf-8")
    store = RunStore(root / "data" / "db.sqlite3")
    record = store.create_run(
        objective="Private workflow",
        acceptance_criteria=[],
        workspace=str(project),
        mode="workspace",
        model=None,
        command_grant={"distribution": "Ubuntu", "profile_sha256": "a" * 64},
        procedure_trial=procedure_trial,
    )
    token = store.acquire_lease(record["id"], "worker", seconds=60)
    store.transition(record["id"], RunState.PLANNING, owner=token)
    store.transition(record["id"], RunState.RUNNING, owner=token)
    return CommandController(store, transport), store, record["id"], token, project


async def test_controller_commits_only_registered_matching_output(workspace_tmp_path):
    controller, store, run_id, token, _ = setup(workspace_tmp_path, FixtureTransport())
    result = await controller.execute(
        run_id,
        token,
        "command",
        {"argv": ["fixture"]},
        idempotency_key="command",
        deadline=time.monotonic() + 15,
    )
    assert result["succeeded"] is True
    assert len(store.list_artifacts(run_id)) == 3
    assert not store.unresolved_tool_calls(run_id)


async def test_final_validation_purpose_is_saved_and_model_commands_are_denied(workspace_tmp_path):
    transport = FixtureTransport()
    _controller, store, run_id, token, _ = setup(workspace_tmp_path, transport)
    store.transition(run_id, RunState.VERIFYING, owner=token)
    validation = CommandController(store, transport, purpose="project_validation")
    result = await validation.execute(
        run_id,
        token,
        "final-check",
        {"argv": ["fixture"]},
        idempotency_key="final-check",
        deadline=time.monotonic() + 15,
    )
    assert result["succeeded"] is True
    reserved = next(
        event["payload"]
        for event in store.list_events(run_id)
        if event["event_type"] == "execution.reserved"
    )
    assert reserved["purpose"] == "project_validation"
    with pytest.raises(PermissionError, match="current run state"):
        CommandJournal(store).reserve(
            run_id, token, "agent-check", {"argv": ["fixture"]}, idempotency_key="agent-check"
        )


async def test_mismatched_output_never_becomes_success(workspace_tmp_path):
    transport = FixtureTransport(corrupt=True)
    controller, store, run_id, token, _ = setup(workspace_tmp_path, transport)
    with pytest.raises(ValueError, match="receipt"):
        await controller.execute(
            run_id,
            token,
            "command",
            {"argv": ["fixture"]},
            idempotency_key="command",
            deadline=time.monotonic() + 15,
        )
    assert transport.controls == ["cleanup", "status"]
    assert not store.unresolved_tool_calls(run_id)
    assert not any(
        event["event_type"] == "execution.completed" for event in store.list_events(run_id)
    )
    assert any(event["event_type"] == "execution.reconciled" for event in store.list_events(run_id))


async def test_failed_snapshot_seals_identity_before_releasing_slot(workspace_tmp_path):
    transport = FixtureTransport()
    controller, store, run_id, token, project = setup(workspace_tmp_path, transport)
    (project / "too-large.txt").write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    with pytest.raises(RuntimeError):
        await controller.execute(
            run_id,
            token,
            "command",
            {"argv": ["fixture"]},
            idempotency_key="command",
            deadline=time.monotonic() + 15,
        )
    assert transport.controls == ["cleanup", "status"]
    assert not store.unresolved_tool_calls(run_id)
    assert not any(event["event_type"] == "tool.executing" for event in store.list_events(run_id))


async def test_command_controller_does_not_launch_on_external_junction_snapshot(
    workspace_tmp_path,
):
    class CountingTransport(FixtureTransport):
        def __init__(self):
            super().__init__()
            self.launches = 0

        async def run(self, intent, snapshot, *, deadline):
            self.launches += 1
            return await super().run(intent, snapshot, deadline=deadline)

    transport = CountingTransport()
    controller, store, run_id, token, project = setup(workspace_tmp_path, transport)
    outside = workspace_tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "private.txt"
    sentinel.write_bytes(b"outside command payload")
    junction = project / "external"
    if os.name == "nt":
        await asyncio.to_thread(
            subprocess.run,
            ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
            check=True,
            capture_output=True,
        )
    else:
        junction.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(RuntimeError, match="Owned transport failed"):
            await controller.execute(
                run_id,
                token,
                "junction-command",
                {"argv": ["fixture"]},
                idempotency_key="junction-command",
                deadline=time.monotonic() + 15,
            )
        events = store.list_events(run_id)
        assert transport.launches == 0
        assert transport.controls == ["cleanup", "status"]
        assert any(event["event_type"] == "execution.reserved" for event in events)
        assert not any(event["event_type"] == "execution.launched" for event in events)
        assert not store.unresolved_tool_calls(run_id)
        assert sentinel.read_bytes() == b"outside command payload"
    finally:
        if os.name == "nt":
            junction.rmdir()
        else:
            junction.unlink()


async def test_expired_controller_budget_creates_no_intent(workspace_tmp_path):
    controller, store, run_id, token, _ = setup(workspace_tmp_path, FixtureTransport())
    with pytest.raises(TimeoutError):
        await controller.execute(
            run_id,
            token,
            "command",
            {"argv": ["fixture"]},
            idempotency_key="command",
            deadline=time.monotonic() - 1,
        )
    assert not store.unresolved_tool_calls(run_id)


async def test_unproved_cleanup_retains_unresolved_admission(workspace_tmp_path):
    class UnavailableCleanup(FixtureTransport):
        async def control(self, action, intent, *, deadline):
            raise TimeoutError("Cannot confirm remote shutdown")

    controller, store, run_id, token, _ = setup(
        workspace_tmp_path, UnavailableCleanup(corrupt=True)
    )
    with pytest.raises(TimeoutError, match="confirm"):
        await controller.execute(
            run_id,
            token,
            "command",
            {"argv": ["fixture"]},
            idempotency_key="command",
            deadline=time.monotonic() + 15,
        )
    assert len(store.unresolved_tool_calls(run_id)) == 1
    assert not any(
        event["event_type"] == "execution.reconciled" for event in store.list_events(run_id)
    )


@pytest.mark.parametrize("damage", [None, "missing", "changed"])
async def test_fresh_controller_recovers_only_valid_persisted_output(workspace_tmp_path, damage):
    class Crash(BaseException):
        pass

    class DurableTransport(FixtureTransport):
        executions = 0
        observed = None

        async def run(self, intent, snapshot, *, deadline):
            self.executions += 1
            self.observed = await super().run(intent, snapshot, deadline=deadline)
            return self.observed

        async def control(self, action, intent, *, deadline):
            reply = await super().control(action, intent, deadline=deadline)
            reply["outcome"] = {"state": "finished", "result": self.observed}
            return reply

    transport = DurableTransport()
    controller, store, run_id, token, _ = setup(workspace_tmp_path, transport)

    def crash(*args, **kwargs):
        raise Crash()

    controller.journal.complete = crash
    with pytest.raises(Crash):
        await controller.execute(
            run_id,
            token,
            "command",
            {"argv": ["fixture"]},
            idempotency_key="command",
            deadline=time.monotonic() + 15,
        )
    if damage:
        metadata = next(
            item
            for item in store.list_artifacts(run_id)
            if item["provenance"].startswith("execution.stdout:")
        )
        path = store.path.parent / "artifacts" / store.get_artifact(metadata["id"])["storage_key"]
        if damage == "missing":
            path.unlink()
        else:
            path.write_bytes(b"damaged")
    store.release_lease(run_id, token)
    reopened = RunStore(store.path)
    replacement = reopened.acquire_lease(run_id, "restarted")
    recovered = CommandController(reopened, transport)
    with pytest.raises(LeaseLost):
        await recovered.recover(run_id, token, "command")
    result = await recovered.recover(run_id, replacement, "command")
    assert transport.executions == 1 and not reopened.unresolved_tool_calls(run_id)
    if damage:
        assert result["verification"] == "not_verified" and "exit_code" not in result
    else:
        assert result["succeeded"] is True and result["result_reconciled"] is True


@pytest.mark.parametrize("project_missing", [False, True])
async def test_recovery_of_prelaunch_crash_never_executes(workspace_tmp_path, project_missing):
    class NoLaunchTransport(FixtureTransport):
        async def run(self, *args, **kwargs):
            pytest.fail("Recovery must never execute the reserved command")

    transport = NoLaunchTransport()
    controller, store, run_id, token, project = setup(workspace_tmp_path, transport)
    controller.journal.reserve(
        run_id, token, "command", {"argv": ["fixture"]}, idempotency_key="command"
    )
    if project_missing:
        (project / "fixture.txt").unlink()
        project.rmdir()
    store.release_lease(run_id, token)
    replacement = store.acquire_lease(run_id, "recovery")
    result = await controller.recover(run_id, replacement, "command")
    assert result["verification"] == "not_verified" and "exit_code" not in result
    assert not store.unresolved_tool_calls(run_id)
