import pytest
from xaa.runtime.command_recovery import StoppedCommandRecovery
from xaa.runtime.states import RunState
from xaa.storage.commands import CommandJournal
from xaa.storage.runs import RunStore


def stopped(root):
    project = root / "project"
    project.mkdir()
    store = RunStore(root / "data" / "db.sqlite3")
    run = store.create_run(
        objective="Interrupted command",
        acceptance_criteria=[],
        workspace=str(project),
        mode="workspace",
        model=None,
        command_grant={"distribution": "Ubuntu", "profile_sha256": "a" * 64},
    )
    token = store.acquire_lease(run["id"], "lost-worker")
    store.transition(run["id"], RunState.PLANNING, owner=token)
    store.transition(run["id"], RunState.RUNNING, owner=token)
    CommandJournal(store).reserve(
        run["id"], token, "command", {"argv": ["fixture"]}, idempotency_key="command"
    )
    with store.connection() as connection:
        connection.execute(
            "UPDATE runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (run["id"],)
        )
    assert store.recover_abandoned_runs() == []
    return store, run["id"]


class Backend:
    def __init__(self, result=False, on_cleanup=None):
        self.result = result
        self.on_cleanup = on_cleanup
        self.calls = []

    async def control(self, action, intent, *, deadline):
        self.calls.append(action)
        if action == "cleanup" and self.on_cleanup:
            self.on_cleanup()
        return {
            "execution_id": intent.execution_id,
            "cleanup_confirmed": True,
            "closed": True,
            "manager_alive": False,
            "outcome": {"state": "finished", "result": {}} if self.result else None,
        }


@pytest.mark.parametrize("available", [False, True])
async def test_stopped_discovery_cleans_without_restarting_run(workspace_tmp_path, available):
    store, run_id = stopped(workspace_tmp_path)
    revision = store.get_run(run_id)["revision"]
    backend = Backend(result=available)

    async def discover(distribution):
        assert distribution == "Ubuntu"
        return backend

    recovery = StoppedCommandRecovery(store, discover)
    assert await recovery.run_once() == 1
    assert backend.calls == ["cleanup", "status"]
    assert store.get_run(run_id)["state"] == "blocked"
    assert store.get_run(run_id)["revision"] == revision
    assert bool(store.unresolved_tool_calls(run_id)) == available
    assert CommandJournal(store).stopped_candidates() == []
    assert await recovery.run_once() == 0
    assert len(store.list_artifacts(run_id)) == 1


async def test_backend_failure_retains_slot_and_backs_off(workspace_tmp_path):
    store, run_id = stopped(workspace_tmp_path)
    calls = []

    async def unavailable(distribution):
        calls.append(distribution)
        raise OSError("diagnostic backend unavailable")

    recovery = StoppedCommandRecovery(store, unavailable)
    assert await recovery.run_once() == 0
    assert await recovery.run_once() == 0
    assert calls == ["Ubuntu"]
    assert len(store.unresolved_tool_calls(run_id)) == 1
    assert not store.list_artifacts(run_id)


async def test_resume_race_cannot_commit_stale_stopped_observation(workspace_tmp_path):
    store, run_id = stopped(workspace_tmp_path)
    backend = Backend(on_cleanup=lambda: store.transition(run_id, RunState.QUEUED))

    async def discover(distribution):
        return backend

    assert await StoppedCommandRecovery(store, discover).run_once() == 0
    assert store.get_run(run_id)["state"] == "queued"
    assert len(store.unresolved_tool_calls(run_id)) == 1
    assert not store.list_artifacts(run_id)


def test_resumed_or_owned_run_is_not_a_stopped_candidate(workspace_tmp_path):
    store, run_id = stopped(workspace_tmp_path)
    prior = CommandJournal(store).stopped_candidates()[0]
    store.transition(run_id, RunState.QUEUED)
    assert CommandJournal(store).stopped_candidates() == []
    token = store.acquire_lease(run_id, "new-owner")
    assert token is not None
    assert CommandJournal(store).stopped_candidates() == []
    with pytest.raises(RuntimeError):
        CommandJournal(store).stopped_intent(run_id, prior["revision"], "command")
