import asyncio
import sqlite3

import xaa.runtime.supervisor as supervisor_module
from xaa.runtime.states import RunState
from xaa.runtime.supervisor import RunSupervisor
from xaa.storage.runs import RunStore


def test_recovery_preserves_live_workers_and_blocks_uncertain_actions(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "recovery.sqlite3")
    ids = []
    for name in ["live", "expired", "uncertain"]:
        run_id = store.create_run(
            objective=name, acceptance_criteria=[], workspace=None, mode="observe", model="fixture"
        )["id"]
        token = store.acquire_lease(run_id, name)
        store.transition(run_id, RunState.PLANNING, owner=token)
        if name == "uncertain":
            store.record_tool_intent(
                run_id,
                token,
                call_id="write",
                tool="write",
                version="1",
                arguments={},
                idempotency_key="write",
            )
        ids.append(run_id)
    connection = sqlite3.connect(store.path)
    try:
        with connection:
            connection.execute(
                "UPDATE runs SET lease_expires_at = '2000-01-01T00:00:00+00:00' WHERE id IN (?, ?)",
                tuple(ids[1:]),
            )
    finally:
        connection.close()
    assert store.recover_abandoned_runs() == [ids[1]]
    assert store.get_run(ids[0])["state"] == "planning"
    assert store.get_run(ids[2])["state"] == "blocked"
    count = len(store.list_events(ids[1]))
    store.recover_abandoned_runs()
    assert len(store.list_events(ids[1])) == count


async def test_supervisor_recovery_survives_transient_store_and_command_errors(
    workspace_tmp_path, monkeypatch, caplog
):
    store = RunStore(workspace_tmp_path / "supervisor-recovery.sqlite3")
    run_id = store.create_run(
        objective="Recover after a transient error",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
    )["id"]
    store_recover = store.recover_abandoned_runs
    store_attempts = 0

    def flaky_store_recovery():
        nonlocal store_attempts
        store_attempts += 1
        if store_attempts == 1:
            raise sqlite3.OperationalError("temporary database read failure")
        return store_recover()

    monkeypatch.setattr(store, "recover_abandoned_runs", flaky_store_recovery)
    monkeypatch.setattr(supervisor_module, "RECOVERY_INTERVAL_SECONDS", 0.005)
    dispatched = asyncio.Event()
    command_recovered = asyncio.Event()
    command_attempts = 0

    class CoordinatorStub:
        def __init__(self):
            self.store = store

        async def execute(self, recovered_run_id):
            token = store.acquire_lease(recovered_run_id, "recovery-test")
            assert token
            store.transition(recovered_run_id, RunState.PLANNING, owner=token)
            store.transition(recovered_run_id, RunState.FAILED, owner=token)
            store.release_lease(recovered_run_id, token)
            dispatched.set()

    class CommandRecoveryStub:
        def __init__(self, _store):
            pass

        async def run_once(self):
            nonlocal command_attempts
            command_attempts += 1
            if command_attempts == 1:
                raise RuntimeError("temporary command recovery failure")
            command_recovered.set()
            return 0

    monkeypatch.setattr(supervisor_module, "StoppedCommandRecovery", CommandRecoveryStub)
    supervisor = RunSupervisor(CoordinatorStub())  # type: ignore[arg-type]
    supervisor.start()
    try:
        await asyncio.wait_for(
            asyncio.gather(dispatched.wait(), command_recovered.wait()), timeout=1
        )
        assert store_attempts >= 2
        assert command_attempts >= 2
        assert store.get_run(run_id)["state"] == RunState.FAILED
        assert supervisor.recovery_task is not None and not supervisor.recovery_task.done()
        assert "Run ownership recovery scan failed" in caplog.text
        assert "Stopped command recovery scan failed" in caplog.text
    finally:
        await supervisor.close()
