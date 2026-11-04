import base64
import hashlib
import time

import pytest
from xaa.runtime.command_controller import CommandController
from xaa.runtime.states import RunState
from xaa.storage.runs import RunStore


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


def setup(root, transport):
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
